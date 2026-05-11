#!/usr/bin/env python3
"""
sttm_recorder.py — record STTM Bani Controller events into a benchmark submission.

This is an "STTM-shaped" fake relay. Your system can drive it with the same
code path it uses to drive a real Gurdwara projector running STTM Desktop —
you just point your STTM client at this server's URL instead of
``api.sikhitothemax.org``. While connected, the recorder buffers the
``shabad`` events your system emits and writes them as a benchmark
submission JSON (the format documented in ``README.md``) when the session
ends. The result is consumed by ``eval.py`` like any other submission.

Wire format
-----------
The protocol mirrors STTM Bani Controller (socket.io v2, ``data`` events
with a ``host`` / ``type`` / ``pin`` envelope). The recorder additionally
requires every ``shabad`` event to include an extension field:

    audio_t : float
        Seconds since the start of the audio file your system is
        processing. STTM Desktop ignores unknown fields, so adding
        ``audio_t`` keeps real-relay compatibility intact.

This is the only protocol extension. ``shabadId``, ``verseId`` and
``lineCount`` are emitted verbatim (the recorder records ``shabadId`` and
``verseId``; ``eval.py`` resolves ``verseId`` to ``line_idx`` via the
ground truth — no BaniDB lookup required here).

Quick start
-----------

    pip install "python-socketio>=5,<6" "aiohttp>=3,<4"

    python sttm_recorder.py \\
        --video-id IZOsmkdmmcg \\
        --out submission/IZOsmkdmmcg.json \\
        --code bench --pin 1234 --port 5051

Your system then connects to ``http://localhost:5051/bench`` (socket.io
v2, namespace ``/bench``), authenticates with ``request-control`` + the
PIN, and emits ``shabad`` events with ``audio_t``.

Either send a ``bench-end`` event (custom) to finalize and exit, or just
disconnect — both will write the JSON.

See ``examples/sttm_submission_example.py`` for a 60-line client.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any

try:
    import socketio
    from aiohttp import web
except ImportError as e:  # pragma: no cover - install hint
    sys.stderr.write(
        "sttm_recorder requires python-socketio and aiohttp:\n"
        '    pip install "python-socketio>=5,<6" "aiohttp>=3,<4"\n'
        f"(import failed: {e})\n"
    )
    sys.exit(1)


# ─── recorder state ─────────────────────────────────────────────────────


class Recorder:
    """Buffers STTM-shaped events into benchmark segments.

    A segment is opened by a ``shabad`` event and closed by:
      - the next ``shabad`` event (segment switches to the new line)
      - a ``text`` / ``bani`` / ``ceremony`` event (system has gone to a
        generic / out-of-scope screen; we close, do not reopen)
      - ``bench-end`` (explicit finalize)
      - disconnect (implicit finalize, using last seen ``audio_t``)
    """

    def __init__(self, video_id: str, out_path: Path) -> None:
        self.video_id = video_id
        self.out_path = out_path
        self.segments: list[dict[str, Any]] = []
        self._open: dict[str, Any] | None = None
        self._last_audio_t: float | None = None
        self.finalized: bool = False

    def _close_open(self, end_t: float) -> None:
        if self._open is None:
            return
        # Drop zero-duration "tap" segments (start == end). Could happen if
        # the SUT emits two events at the same audio_t.
        if end_t > self._open["start"]:
            self.segments.append({**self._open, "end": round(end_t, 3)})
        self._open = None

    def handle_shabad(self, payload: dict[str, Any]) -> str | None:
        audio_t = _coerce_audio_t(payload)
        if audio_t is None:
            return "shabad event missing audio_t"
        shabad_id = _coerce_int(payload, ("shabadId", "shabadid", "id"))
        verse_id = _coerce_int(payload, ("verseId", "highlight"))
        if shabad_id is None or verse_id is None:
            return "shabad event missing shabadId / verseId"
        self._last_audio_t = audio_t
        # Same (shabad, verse) as currently open? No-op — segment just
        # gets a longer implicit duration.
        if (
            self._open is not None
            and self._open["shabad_id"] == shabad_id
            and self._open["verse_id"] == verse_id
        ):
            return None
        self._close_open(audio_t)
        self._open = {
            "start": round(audio_t, 3),
            "shabad_id": shabad_id,
            "verse_id": verse_id,
        }
        return None

    def handle_silence(self, payload: dict[str, Any]) -> str | None:
        """text / bani / ceremony / bench-end — close, do not reopen."""
        audio_t = _coerce_audio_t(payload)
        if audio_t is None:
            return "event missing audio_t"
        self._last_audio_t = audio_t
        self._close_open(audio_t)
        return None

    def finalize(self) -> dict[str, Any]:
        """Write JSON and mark finalized. Idempotent.

        Returns ``{'wrote': bool, 'path': str, 'segments': int}``. The
        ``wrote`` flag is True only on the first call — callers should use
        it to gate any 'wrote N segments' log so repeat calls (e.g. from
        both bench-end and a follow-on disconnect) stay quiet.
        """
        if self.finalized:
            return {
                "wrote": False,
                "path": str(self.out_path),
                "segments": len(self.segments),
            }
        if self._open is not None and self._last_audio_t is not None:
            self._close_open(self._last_audio_t)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        submission = {"video_id": self.video_id, "segments": self.segments}
        self.out_path.write_text(
            json.dumps(submission, indent=2, ensure_ascii=False)
        )
        self.finalized = True
        return {
            "wrote": True,
            "path": str(self.out_path),
            "segments": len(self.segments),
        }


def _coerce_audio_t(payload: dict[str, Any]) -> float | None:
    try:
        return float(payload["audio_t"])
    except (KeyError, TypeError, ValueError):
        return None


def _coerce_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for k in keys:
        if k in payload and payload[k] is not None:
            try:
                return int(payload[k])
            except (TypeError, ValueError):
                continue
    return None


# ─── server wiring ──────────────────────────────────────────────────────


def build_app(
    args: argparse.Namespace,
    shutdown_event: asyncio.Event | None = None,
) -> tuple[web.Application, Recorder]:
    rec = Recorder(args.video_id, Path(args.out))
    sio = socketio.AsyncServer(
        async_mode="aiohttp",
        cors_allowed_origins="*",
        # STTM Desktop and Web both use socket.io-client 2.x → EIO=3.
        # python-socketio v5 supports v2 clients natively.
        logger=False,
        engineio_logger=False,
    )
    app = web.Application()
    sio.attach(app)

    namespace = "/" + args.code.lstrip("/")
    pin = int(args.pin)

    def _signal_shutdown() -> None:
        if shutdown_event is not None and not shutdown_event.is_set():
            shutdown_event.set()

    # Reply to the SUT echo-suppression check used by STTM-shaped clients:
    # they treat `host == "sttm-desktop"` (anything that isn't them) as
    # the relay echo. We play that role for response-control and any
    # passthrough we ever decide to emit back.
    HOST_LABEL = "sttm-desktop"

    @sio.event(namespace=namespace)
    async def connect(sid, environ):
        print(f"[recorder] client connected sid={sid}")

    @sio.event(namespace=namespace)
    async def disconnect(sid):
        print(f"[recorder] client disconnected sid={sid}")
        info = rec.finalize()
        if info["wrote"]:
            print(
                f"[recorder] wrote {info['path']} ({info['segments']} segments)"
            )
        # Stop the server after the first session finalizes. One-shot by
        # design — for multi-video runs, loop the recorder externally.
        _signal_shutdown()

    @sio.on("data", namespace=namespace)
    async def on_data(sid, payload):
        if not isinstance(payload, dict):
            return
        # Ignore anything the recorder might have echoed; we never do, but
        # be defensive against loopback configurations.
        if payload.get("host") == HOST_LABEL:
            return
        ev_type = payload.get("type")

        if ev_type == "request-control":
            ok = int(payload.get("pin", 0)) == pin
            await sio.emit(
                "data",
                {
                    "host": HOST_LABEL,
                    "type": "response-control",
                    # STTM Desktop emits `success: <pin>` (truthy int) — keep
                    # parity so clients that check `evt.success` truthiness
                    # are satisfied either way.
                    "success": pin if ok else False,
                    "settings": {"fontSizes": {}},
                },
                namespace=namespace,
                to=sid,
            )
            print(f"[recorder] auth {'ok' if ok else 'FAIL'}")
            return

        if ev_type == "shabad":
            err = rec.handle_shabad(payload)
            if err:
                print(f"[recorder] WARN: dropping shabad event: {err}")
            else:
                t = rec._last_audio_t
                print(
                    f"[recorder] t={t:.2f} sid={payload.get('shabadId')} "
                    f"vid={payload.get('verseId')}"
                )
            return

        if ev_type in ("text", "bani", "ceremony", "bench-end"):
            err = rec.handle_silence(payload)
            note = "bench-end" if ev_type == "bench-end" else f"silence ({ev_type})"
            if err:
                print(f"[recorder] WARN: dropping {note}: {err}")
            else:
                print(f"[recorder] t={rec._last_audio_t:.2f} {note} — closed segment")
            if ev_type == "bench-end":
                info = rec.finalize()
                if info["wrote"]:
                    print(
                        f"[recorder] bench-end → wrote {info['path']} "
                        f"({info['segments']} segments)"
                    )
                _signal_shutdown()
            return

        # Unknown type — log once. STTM has request-control / response-control /
        # shabad / bani / ceremony / text / settings — anything else is an
        # extension the recorder doesn't understand.
        print(f"[recorder] ignoring unknown event type: {ev_type!r}")

    # ── HTTP compatibility shims for STTM-style clients ────────────────
    # sttm-web pings these before opening the socket; sttm-desktop pings
    # /sync/begin to get a namespace. The recorder pins to one configured
    # code, so we just echo it back / 200 / 404 as appropriate.

    async def sync_begin(request: web.Request) -> web.Response:
        return web.json_response({"data": {"namespaceString": args.code}})

    async def sync_join(request: web.Request) -> web.Response:
        if request.match_info["code"] == args.code:
            return web.json_response({"data": {"ok": True}})
        return web.json_response({"error": "unknown code"}, status=404)

    async def sync_end(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app.router.add_get("/sync/begin/{host}", sync_begin)
    app.router.add_get("/sync/join/{code}", sync_join)
    app.router.add_post("/sync/end/{code}", sync_end)
    app.router.add_get(
        "/",
        lambda r: web.Response(
            text=(
                f"sttm_recorder up — code={args.code} pin={args.pin} "
                f"video_id={args.video_id} out={args.out}\n"
            )
        ),
    )

    return app, rec


# ─── CLI entry point ────────────────────────────────────────────────────


async def _run(args: argparse.Namespace) -> int:
    shutdown_event = asyncio.Event()
    app, rec = build_app(args, shutdown_event=shutdown_event)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=args.host, port=args.port)
    await site.start()

    print(
        f"[recorder] listening on http://{args.host}:{args.port}/  "
        f"namespace=/{args.code}  video_id={args.video_id}  pin={args.pin}"
    )
    print(f"[recorder] will write {args.out} on bench-end or disconnect")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            # add_signal_handler isn't supported on every platform (Windows).
            # signal.signal() fallback is best-effort.
            signal.signal(sig, lambda *_: shutdown_event.set())

    try:
        await shutdown_event.wait()
    finally:
        # bench-end / disconnect already finalized; this just covers the
        # SIGINT-before-any-client-connected case.
        info = rec.finalize()
        if info["wrote"]:
            print(
                f"[recorder] wrote {info['path']} ({info['segments']} segments)"
            )
        await runner.cleanup()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--video-id", required=True, help="video_id for the GT case being benchmarked")
    ap.add_argument("--out", required=True, help="path to write submission JSON")
    ap.add_argument("--code", default="bench", help="sync code / socket.io namespace (default: bench)")
    ap.add_argument("--pin", default="1234", help="PIN your system will auth with (default: 1234)")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    ap.add_argument("--port", type=int, default=5051, help="HTTP bind port (default: 5051)")
    args = ap.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
