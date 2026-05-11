#!/usr/bin/env python3
"""
Minimal STTM-shaped submission client for the Live Captioning for
Gurbani Kirtan benchmark.

Demonstrates the wire format expected by ``sttm_recorder.py``. The
events emitted here use exactly the same shape your system would emit
to a real STTM Desktop Bani Controller running in a Gurdwara — the only
extension is the ``audio_t`` field on ``shabad`` events (STTM Desktop
ignores unknown fields, so the same client code works against both).

This example doesn't run a model — it replays three hard-coded segments
to show what your system should be emitting. Replace the
``hardcoded_emissions`` generator with output from your real system.

Run, in two terminals:

    # 1) start the recorder, pointing at one GT case
    python sttm_recorder.py \\
        --video-id IZOsmkdmmcg \\
        --out /tmp/sttm_demo/IZOsmkdmmcg.json \\
        --code bench --pin 1234 --port 5051

    # 2) run this example client
    pip install "python-socketio[asyncio_client]>=5,<6"
    python examples/sttm_submission_example.py \\
        --url http://localhost:5051 --code bench --pin 1234

The recorder will write ``/tmp/sttm_demo/IZOsmkdmmcg.json``, scoreable
by ``eval.py`` like any other submission.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

try:
    import socketio
except ImportError:
    sys.stderr.write(
        'install: pip install "python-socketio[asyncio_client]>=5,<6"\n'
    )
    sys.exit(1)


# Hard-coded emissions for the demo. In a real submitter, each (audio_t,
# shabad_id, verse_id) here would come from your model's online output.
DEMO_EMISSIONS: list[tuple[float, int, int]] = [
    # audio_t (s),  shabadId,  verseId
    (28.0, 4377, 52522),  # line 1 starts
    (41.3, 4377, 52523),  # switch to line 2 (rahau)
    (69.6, 4377, 52522),  # back to line 1
]


async def run(url: str, code: str, pin: int) -> None:
    sio = socketio.AsyncClient(reconnection=False)
    authed = asyncio.Event()

    @sio.on("data", namespace="/" + code)
    async def on_data(payload):
        # The recorder echoes back response-control with host="sttm-desktop".
        # That's our signal that auth succeeded.
        if payload.get("type") == "response-control":
            if payload.get("success"):
                print("[client] auth ok")
                authed.set()
            else:
                print("[client] auth FAILED (bad PIN?)")

    await sio.connect(
        url,
        namespaces=["/" + code],
        # STTM clients use socket.io v2 in production. python-socketio v5
        # client speaks both v2 (EIO=3) and v4 (EIO=4); we match the
        # recorder's auto-detected protocol, which is v4 here. A real
        # submitter wrapping STTM Desktop code would use v2 — same wire
        # payload shape, recorder handles both.
    )
    await sio.emit(
        "data",
        {"host": "sttm-web", "type": "request-control", "pin": pin},
        namespace="/" + code,
    )

    try:
        await asyncio.wait_for(authed.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        print("[client] no response-control within 5s — giving up")
        await sio.disconnect()
        return

    for audio_t, shabad_id, verse_id in DEMO_EMISSIONS:
        await sio.emit(
            "data",
            {
                "host": "sttm-web",
                "type": "shabad",
                "pin": pin,
                "shabadId": shabad_id,
                "verseId": verse_id,
                "lineCount": 0,        # required by real STTM Desktop; recorder ignores
                "audio_t": audio_t,    # ← REQUIRED extension for the recorder
            },
            namespace="/" + code,
        )
        print(f"[client] emit t={audio_t:.2f} sid={shabad_id} vid={verse_id}")
        await asyncio.sleep(0.05)

    # Tell the recorder to finalize. The final emission's audio_t will be
    # used as the end of the last open segment.
    await sio.emit(
        "data",
        {
            "host": "sttm-web",
            "type": "bench-end",
            "pin": pin,
            "audio_t": DEMO_EMISSIONS[-1][0] + 20.0,  # claim the last line ran for 20 more seconds
        },
        namespace="/" + code,
    )
    print("[client] sent bench-end; recorder will write JSON and exit")
    await sio.wait()


def main() -> int:
    ap = argparse.ArgumentParser(description="Minimal STTM-shaped submission client.")
    ap.add_argument("--url", default="http://localhost:5051", help="recorder base URL")
    ap.add_argument("--code", default="bench", help="sync code (= namespace)")
    ap.add_argument("--pin", type=int, default=1234, help="PIN")
    args = ap.parse_args()
    try:
        asyncio.run(run(args.url, args.code, args.pin))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
