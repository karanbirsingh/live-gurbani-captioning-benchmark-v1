#!/usr/bin/env python3
"""
Minimal submission example for the Live Captioning for Gurbani Kirtan
benchmark.

Writes one prediction file per ground-truth case, in the correct submission
format. The predictions here are trivial (null everywhere) — replace the
`predict()` function with a call to your own model.

Run, then score:

    python examples/minimal_submission.py
    python eval.py --pred /tmp/kirtan_captioning_submission --gt test/

The empty-prediction baseline scores around 26% (credit for correctly
predicting `null` during gaps), which is the floor for this benchmark.
"""

import json
import pathlib


def predict(gt: dict) -> list[dict]:
    """Produce segments for one GT case.

    Your model gets:
      - gt["video_id"]       : str, matches a 16kHz mono WAV you fetched
      - gt["shabad_id"]      : NOT available to your model (this is the
                               label being tested — ignore it)
      - gt["total_duration"] : float seconds; feel free to use this to bound
                               your output, or ignore it
      - gt["uem"]            : optional scored-region hint; non-UEM frames
                               are ignored by the scorer, so predictions
                               outside UEM cost nothing

    Canonical SGGS text for `(shabad_id, line_idx)` is not shipped with
    this benchmark — look it up via BaniDB / SikhiToTheMax if your
    system needs it.

    Return a list of segments:
      [{"start": float, "end": float, "line_idx": int}, ...]

    Constraints:
      - start < end, both in seconds
      - line_idx is an integer line index within the predicted shabad;
        if your system is producing the wrong shabad's lines, it will score
        poorly — that's intentional.
      - Segments may overlap; later segments in the list win per-frame.
      - Gaps (unsegmented regions) are interpreted as `null` predictions,
        which are accepted inside GT gaps and penalised inside GT segments.
    """
    # Replace this with your model. For the empty baseline:
    return []


def main() -> None:
    gt_dir = pathlib.Path(__file__).resolve().parents[1] / "test"
    out_dir = pathlib.Path("/tmp/kirtan_captioning_submission")
    out_dir.mkdir(parents=True, exist_ok=True)

    for gt_file in sorted(gt_dir.glob("*.json")):
        gt = json.loads(gt_file.read_text())
        segments = predict(gt)
        submission = {
            "video_id": gt["video_id"],
            "segments": segments,
        }
        out_path = out_dir / gt_file.name
        out_path.write_text(json.dumps(submission, indent=2, ensure_ascii=False))
        print(f"wrote {out_path} ({len(segments)} segments)")


if __name__ == "__main__":
    main()
