#!/usr/bin/env python3
"""
Scorer for the Live Captioning for Gurbani Kirtan benchmark.

Scores predicted line-level segments against ground truth.
Uses frame-level accuracy at 1s resolution with configurable collar.

Usage:
    python eval.py --pred predictions/ --gt test/
    python eval.py --pred predictions/h1ryUzzw6mI.json --gt test/h1ryUzzw6mI.json
    python eval.py --pred predictions/ --gt test/ --collar 2 --verbose

Input format (both pred and GT):
{
    "video_id": "h1ryUzzw6mI",
    "shabad_id": 63,
    "total_duration": 435.0,
    "uem": {"start": 28.0, "end": 430.0},       # GT only — scored region
    "lines": [                                     # GT only — reference text
        {"line_idx": 0, "text": "ਸਿਰੀਰਾਗੁ ਮਹਲਾ ੧ ॥"},
        ...
    ],
    "segments": [
        {"start": 28.0, "end": 45.0, "line_idx": 1},
        {"start": 48.0, "end": 62.0, "line_idx": 2},
        ...
    ]
}

Segments may overlap or have gaps. Gaps default to null (no prediction).
If segments overlap, the later segment wins.
"""

import argparse
import json
import sys
from pathlib import Path


def segments_to_frames(segments: list[dict], total_seconds: int) -> list[int | None]:
    """Convert segments to 1-per-second frame array. Later segments overwrite earlier ones.

    Segments without a `line_idx` are skipped (leaving the frame as None). The
    visualizer should use `pred_segments_to_frames` instead so that pred
    segments which carry only canonical ids/text get resolved against GT.
    """
    frames: list[int | None] = [None] * total_seconds
    for seg in segments:
        line_idx = seg.get("line_idx")
        if line_idx is None:
            continue
        start = int(seg["start"])
        end = min(int(seg["end"]), total_seconds)
        for t in range(start, end):
            frames[t] = line_idx
    return frames


def _build_gt_lookups(
    gt: dict,
) -> tuple[set[tuple[int, int]], dict[int, int], dict[str, int], set[int]]:
    """Build (shabad_line_set, verse_id_map, text_map, line_set) from GT.

    All keyed on the GT's own ``shabad_id``, so a pred segment on a
    different shabad cannot accidentally resolve via ``(shabad_id,
    line_idx)``. ``line_set`` is used for the documented-format fallback
    where pred provides ``line_idx`` only (no ``shabad_id`` anywhere) —
    in that case we accept any GT line index for this case's shabad.
    """
    by_shabad_line: set[tuple[int, int]] = set()
    by_verse_id: dict[int, int] = {}
    by_text: dict[str, int] = {}
    by_line: set[int] = set()
    gt_shabad_id = gt.get("shabad_id")
    if gt_shabad_id is None:
        return by_shabad_line, by_verse_id, by_text, by_line
    for src in (gt.get("lines", []), gt.get("segments", [])):
        for entry in src:
            li = entry.get("line_idx")
            if li is None:
                continue
            by_shabad_line.add((int(gt_shabad_id), int(li)))
            by_line.add(int(li))
            vid = entry.get("verse_id")
            if vid is not None:
                by_verse_id[int(vid)] = int(li)
            txt = entry.get("banidb_gurmukhi")
            if txt:
                by_text[txt] = int(li)
    return by_shabad_line, by_verse_id, by_text, by_line


# Sentinel used when a pred segment cannot be resolved to any GT line.
# Distinguished from None (unscored / no prediction) so it counts as wrong.
NO_MATCH: str = "__no_match__"


def _resolve_pred_label(
    seg: dict,
    by_shabad_line: set[tuple[int, int]],
    by_verse_id: dict[int, int],
    by_text: dict[str, int],
    by_line: set[int],
) -> object:
    """Resolve a pred segment to GT's line_idx.

    Resolution order, first hit wins:
      1) ``(shabad_id, line_idx)``    — both sides name the shabad
      2) ``verse_id``                 — canonical BaniDB Alliance pangti id
      3) ``banidb_gurmukhi``          — verbatim spaced unicode pangti text
      4) ``line_idx`` alone           — documented submission format,
                                        only used when pred provides
                                        no ``shabad_id`` at all

    A pred segment that *does* name a ``shabad_id`` but the wrong one
    will not fall through to (4) — it returns ``NO_MATCH``, preserving
    the wrong-shabad penalty documented in the README.

    Anything that doesn't resolve returns ``NO_MATCH`` and is scored as
    wrong.
    """
    seg_shabad = seg.get("shabad_id")
    seg_line = seg.get("line_idx")
    if (
        seg_shabad is not None
        and seg_line is not None
        and (int(seg_shabad), int(seg_line)) in by_shabad_line
    ):
        return int(seg_line)
    seg_vid = seg.get("verse_id")
    if seg_vid is not None and int(seg_vid) in by_verse_id:
        return by_verse_id[int(seg_vid)]
    seg_txt = seg.get("banidb_gurmukhi")
    if seg_txt and seg_txt in by_text:
        return by_text[seg_txt]
    # Documented-format fallback: line_idx only, no shabad_id claimed.
    # If pred named a shabad_id and it didn't match in path (1), we
    # deliberately do NOT fall through here — that's the wrong-shabad case.
    if (
        seg_shabad is None
        and seg_line is not None
        and int(seg_line) in by_line
    ):
        return int(seg_line)
    return NO_MATCH


def pred_segments_to_frames(
    pred_segments: list[dict], gt: dict, total_seconds: int,
    pred_top_level: dict | None = None,
) -> list[object | None]:
    """Pred-side analog of `segments_to_frames`.

    Each frame holds either a GT `line_idx` (int) when the pred segment
    resolves, `NO_MATCH` (str sentinel) when it can't, or None where the
    pred is silent. Used by both `score_video` and the visualizer so the
    rendered pred strip exactly matches what is being scored.

    If ``pred_top_level`` carries a ``shabad_id`` at the top level (a
    convenience some submitters use — declare it once instead of on
    every segment), it is propagated into each segment before label
    resolution. Per-segment ``shabad_id`` always wins.
    """
    by_shabad_line, by_verse_id, by_text, by_line = _build_gt_lookups(gt)
    top_sid = None
    if pred_top_level is not None:
        top_sid = pred_top_level.get("shabad_id")
    frames: list[object | None] = [None] * total_seconds
    for seg in pred_segments:
        if top_sid is not None and seg.get("shabad_id") is None:
            seg = {**seg, "shabad_id": top_sid}
        label = _resolve_pred_label(
            seg, by_shabad_line, by_verse_id, by_text, by_line
        )
        start = int(seg["start"])
        end = min(int(seg["end"]), total_seconds)
        for t in range(start, end):
            frames[t] = label
    return frames


def score_video(gt: dict, pred: dict, collar: int = 1, score_gaps: bool = True) -> dict:
    """Score a single video's predictions against ground truth.

    Unified scoring: every frame in UEM is scored.
    - Segment interior: must match GT label exactly
    - Collar (within `collar` seconds of a GT boundary): accept adjacent line or null
    - Gap (between segments): accept adjacent lines or null
    - Anything else is an error

    Pred segments are resolved to GT's line_idx via `_resolve_pred_label`
    (see its docstring for the resolution order). Any pred that can't be
    resolved gets the `NO_MATCH` sentinel and is scored as wrong — no
    special wrong-shabad branch needed.
    """
    uem_start = int(gt.get("uem", {}).get("start", 0))
    uem_end = int(gt.get("uem", {}).get("end", gt["total_duration"]))
    total_seconds = int(gt["total_duration"])

    gt_frames = segments_to_frames(gt["segments"], total_seconds)
    pred_frames = pred_segments_to_frames(
        pred.get("segments", []), gt, total_seconds, pred_top_level=pred
    )

    segments = sorted(gt["segments"], key=lambda s: s["start"])
    
    # Pre-compute acceptable labels for each frame
    # For boundary/gap frames: {line_before, line_after, None}
    boundary_acceptable = {}
    
    for gi in range(len(segments)):
        seg = segments[gi]
        seg_start = int(seg["start"])
        seg_end = int(seg["end"])
        before_label = segments[gi - 1]["line_idx"] if gi > 0 else None
        after_label = segments[gi + 1]["line_idx"] if gi < len(segments) - 1 else None
        
        # Collar at start of segment
        for t in range(max(0, seg_start - collar), seg_start + collar):
            acceptable = {seg["line_idx"], None}
            if before_label is not None:
                acceptable.add(before_label)
            boundary_acceptable[t] = acceptable
        
        # Collar at end of segment
        for t in range(max(0, seg_end - collar), seg_end + collar):
            acceptable = {seg["line_idx"], None}
            if after_label is not None:
                acceptable.add(after_label)
            boundary_acceptable[t] = acceptable
    
    # Gaps between segments: accept adjacent lines or null
    for gi in range(len(segments) - 1):
        gap_start = int(segments[gi]["end"])
        gap_end = int(segments[gi + 1]["start"])
        if gap_end <= gap_start:
            continue
        before_label = segments[gi]["line_idx"]
        after_label = segments[gi + 1]["line_idx"]
        for t in range(gap_start, gap_end):
            boundary_acceptable[t] = {before_label, after_label, None}
    
    correct = 0
    total = 0
    details = []
    
    for t in range(uem_start, min(uem_end, total_seconds)):
        gt_label = gt_frames[t]
        pred_label = pred_frames[t] if t < len(pred_frames) else None
        total += 1
        
        if gt_label is not None and pred_label == gt_label:
            # Exact match on a labeled frame
            correct += 1
            details.append({"t": t, "gt": gt_label, "pred": pred_label, "correct": True, "type": "exact"})
        elif t in boundary_acceptable:
            # Boundary/gap frame — check acceptable set
            if pred_label in boundary_acceptable[t]:
                correct += 1
                details.append({"t": t, "gt": gt_label, "pred": pred_label, "correct": True, 
                               "type": "boundary_ok"})
            else:
                details.append({"t": t, "gt": gt_label, "pred": pred_label, "correct": False,
                               "type": "boundary_error"})
        elif gt_label is None:
            # Unlabeled frame outside any gap (before first seg, after last seg)
            # Accept anything
            correct += 1
            details.append({"t": t, "gt": gt_label, "pred": pred_label, "correct": True, "type": "unscored"})
        else:
            # Interior of segment, wrong prediction
            details.append({"t": t, "gt": gt_label, "pred": pred_label, "correct": False, "type": "error"})
    
    accuracy = correct / total if total > 0 else 0.0
    
    return {
        "video_id": gt["video_id"],
        "shabad_id": gt.get("shabad_id"),
        "frame_accuracy": round(accuracy * 100, 2),
        "correct": correct,
        "total": total,
        "uem_start": uem_start,
        "uem_end": uem_end,
        "n_pred_segments": len(pred.get("segments", [])),
        "n_gt_segments": len(gt["segments"]),
        "details": details,
    }


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Scorer for the Live Captioning for Gurbani Kirtan benchmark"
    )
    parser.add_argument("--pred", required=True, help="Prediction file or directory")
    parser.add_argument("--gt", required=True, help="Ground truth file or directory")
    parser.add_argument("--collar", type=int, default=1, help="Collar in seconds around boundaries (default: 1)")
    parser.add_argument("--verbose", action="store_true", help="Print per-frame details")
    parser.add_argument("--output", help="Save results to JSON file")
    args = parser.parse_args()
    
    pred_path = Path(args.pred)
    gt_path = Path(args.gt)
    
    # Collect file pairs
    if pred_path.is_file() and gt_path.is_file():
        pairs = [(gt_path, pred_path)]
    elif pred_path.is_dir() and gt_path.is_dir():
        gt_files = {f.stem: f for f in gt_path.glob("*.json")}
        pred_files = {f.stem: f for f in pred_path.glob("*.json")}
        common = sorted(set(gt_files) & set(pred_files))
        if not common:
            print("ERROR: No matching files between pred and gt directories")
            sys.exit(1)
        pairs = [(gt_files[k], pred_files[k]) for k in common]
        missing = set(gt_files) - set(pred_files)
        if missing:
            print(f"WARNING: {len(missing)} GT files have no predictions: {', '.join(sorted(missing))}")
    else:
        print("ERROR: --pred and --gt must both be files or both be directories")
        sys.exit(1)
    
    # Score each pair
    results = []
    total_correct = 0
    total_frames = 0
    
    for gt_file, pred_file in pairs:
        gt = load_json(gt_file)
        pred = load_json(pred_file)
        
        result = score_video(gt, pred, collar=args.collar)
        result["stem"] = gt_file.stem
        results.append(result)
        total_correct += result["correct"]
        total_frames += result["total"]
        
        print(f"  {gt_file.stem}: {result['frame_accuracy']:.1f}% "
              f"({result['correct']}/{result['total']} frames, "
              f"{result['n_pred_segments']} pred segs, collar={args.collar}s)")
        
        if args.verbose:
            errors = [d for d in result["details"] if not d["correct"]]
            collar_saves = [d for d in result["details"] if d["type"] == "collar"]
            print(f"    Errors: {len(errors)}, Collar saves: {len(collar_saves)}")
            for d in errors[:10]:
                print(f"    t={d['t']:>4}s: pred={d['pred']} gt={d['gt']}")
            if len(errors) > 10:
                print(f"    ... and {len(errors) - 10} more errors")
    
    # Summary
    overall = total_correct / total_frames * 100 if total_frames > 0 else 0
    print(f"\n{'='*50}")
    print(f"Overall: {overall:.1f}% frame accuracy "
          f"({total_correct}/{total_frames} frames, "
          f"{len(results)} videos, collar={args.collar}s)")
    
    if args.output:
        summary = {
            "collar": args.collar,
            "overall_accuracy": round(overall, 2),
            "total_correct": total_correct,
            "total_frames": total_frames,
            "n_videos": len(results),
            "per_video": [{k: v for k, v in r.items() if k != "details"} for r in results],
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
