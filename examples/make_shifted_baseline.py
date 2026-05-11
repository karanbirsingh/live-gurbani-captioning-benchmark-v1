#!/usr/bin/env python3
"""
Generate a "shifted" baseline by delaying every GT segment by a fixed
number of seconds.

This is a useful smoke test of the scorer: a realistic ASR pipeline lags
the audio by some fraction of a second to a few seconds depending on
window size and model latency, and the resulting submission JSON should
score well below `perfect/` but well above `empty/`.

The committed ``baselines/shifted_5s/`` was produced with::

    python examples/make_shifted_baseline.py --shift 5

For different shift values::

    python examples/make_shifted_baseline.py --shift 2  --out /tmp/shifted_2s
    python examples/make_shifted_baseline.py --shift 10 --out /tmp/shifted_10s

Then::

    python eval.py --pred /tmp/shifted_2s  --gt test/   # expect ~96%
    python eval.py --pred /tmp/shifted_10s --gt test/   # expect ~69%

The shape of that curve as a function of ``--shift`` is a useful
"is the scorer behaving sanely" sanity check after any change to
``eval.py``.
"""

from __future__ import annotations

import argparse
import json
import pathlib


def shift_segments(segments: list[dict], shift_s: float) -> list[dict]:
    """Return segments shifted by ``shift_s`` seconds.

    Negative shifts are clamped to ``start >= 0`` (a segment that would
    start before t=0 just starts at 0). Segments are emitted in the
    documented "line_idx only" format — no ``shabad_id`` or ``verse_id``
    fields, to mirror the simplest possible submitter.
    """
    out = []
    for s in segments:
        new_start = max(0.0, float(s["start"]) + shift_s)
        new_end = max(new_start, float(s["end"]) + shift_s)
        out.append({"start": new_start, "end": new_end, "line_idx": int(s["line_idx"])})
    return out


def main() -> None:
    here = pathlib.Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shift", type=float, default=5.0, help="seconds to delay (default: 5.0)")
    ap.add_argument("--gt", default=str(here.parent / "test"), help="GT directory")
    ap.add_argument(
        "--out",
        default=None,
        help="output directory (default: baselines/shifted_{shift}s/)",
    )
    args = ap.parse_args()

    gt_dir = pathlib.Path(args.gt)
    if args.out is None:
        suffix = f"shifted_{int(args.shift)}s" if args.shift == int(args.shift) else f"shifted_{args.shift}s"
        out_dir = here.parent / "baselines" / suffix
    else:
        out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    n = 0
    for gt_path in sorted(gt_dir.glob("*.json")):
        gt = json.loads(gt_path.read_text())
        sub = {
            "video_id": gt["video_id"],
            "segments": shift_segments(gt["segments"], args.shift),
        }
        (out_dir / gt_path.name).write_text(json.dumps(sub, indent=2, ensure_ascii=False))
        n += 1
    print(f"wrote {n} submission files to {out_dir} (shift={args.shift:+.1f}s)")


if __name__ == "__main__":
    main()
