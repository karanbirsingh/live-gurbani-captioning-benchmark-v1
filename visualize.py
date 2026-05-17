#!/usr/bin/env python3
"""
Render a submission as an HTML tile page, next to ground truth.

Usage:
    python visualize.py --pred baselines/perfect/ --gt test/ --out tiles.html
    python visualize.py --pred my_submission/ --gt test/ --out tiles.html \\
        --audio-dir audio/ --title "my model v2"

Output: a single self-contained HTML file. One tile per GT case.
Each tile has three horizontal strips:
  - GT   (what was actually sung)
  - Pred (what the system said)
  - Diff (green = correct per 1s frame, red = wrong, grey = unscored)

If --audio-dir is given and contains `{video_id}_16k.wav` (or .wav/.mp3),
an <audio> player is embedded per tile.

Standard library only. No deps.
"""

import argparse
import base64
import html
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

from eval import NO_MATCH, pred_segments_to_frames, segments_to_frames, score_video


BANIDB_API = "https://api.banidb.com/v2"


def fetch_shabad_lines(shabad_id, timeout=15):
    """Return {line_idx: gurmukhi_unicode} for a BaniDB shabad. None on failure."""
    url = f"{BANIDB_API}/shabads/{shabad_id}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  warn: fetch shabad {shabad_id} failed: {e}", file=sys.stderr)
        return None
    out = {}
    for i, v in enumerate(data.get("verses", [])):
        text = v.get("verse", {}).get("unicode", "") or v.get("verse", {}).get("gurmukhi", "")
        out[i] = text
    return out


def load_or_fetch_lines(gt_docs, cache_path, do_fetch=True):
    """Load cache from disk, fetch any missing shabad_ids, persist, return dict.
    Keys in cache JSON are stringified shabad ids; returned dict uses ints.
    """
    cache_raw = {}
    if cache_path and cache_path.exists():
        try:
            cache_raw = json.loads(cache_path.read_text())
        except json.JSONDecodeError:
            print(f"  warn: {cache_path} is not valid JSON, starting fresh", file=sys.stderr)
    cache = {int(sid): {int(k): v for k, v in lines.items()}
             for sid, lines in cache_raw.items()}
    needed = {gt["shabad_id"] for gt in gt_docs if "shabad_id" in gt}
    missing = sorted(sid for sid in needed if sid not in cache)
    if missing and do_fetch:
        print(f"fetching {len(missing)} shabad(s) from BaniDB ({BANIDB_API})...",
              file=sys.stderr)
        for sid in missing:
            lines = fetch_shabad_lines(sid)
            if lines is not None:
                cache[sid] = lines
            time.sleep(0.2)  # be polite
        if cache_path:
            cache_path.write_text(json.dumps(
                {str(sid): {str(k): v for k, v in lines.items()}
                 for sid, lines in cache.items()},
                ensure_ascii=False, indent=2) + "\n")
            print(f"  cached to {cache_path}", file=sys.stderr)
    elif missing:
        print(f"  {len(missing)} shabad(s) missing from cache; --no-fetch set, skipping",
              file=sys.stderr)
    return cache


# Distinct but muted palette for line indices (0..N). Cycles after 12.
LINE_COLORS = [
    "#4f7ca8", "#9b6a9e", "#6aa67a", "#b08454", "#8b6fb8", "#a76d6d",
    "#5c9fa8", "#8f9c54", "#a17eac", "#6c8bb5", "#b48ead", "#74a67d",
]
NULL_COLOR = "rgba(255,255,255,0.06)"


def color_for_line(idx):
    if idx is None:
        return NULL_COLOR
    if idx == NO_MATCH:
        # Hatched red so unresolved pred segments are visually distinct from
        # both null (silent) and any valid line color.
        return "repeating-linear-gradient(45deg, #b04545, #b04545 4px, #7a2e2e 4px, #7a2e2e 8px)"
    return LINE_COLORS[idx % len(LINE_COLORS)]


def render_strip(frames, total_seconds, row_class="", lines_by_idx=None, text_by_frame=None):
    """Render a row of 1s frames as inline-block spans (percentage width).

    Tooltip text resolution per frame:
      1) `text_by_frame[t]` if set (used for pred frames so each segment can
         carry the pangti text its producer emitted, even if the eval
         couldn't resolve it to a GT line)
      2) `lines_by_idx[label]` for resolved frames (BaniDB API cache)
      3) a placeholder string
    """
    lines_by_idx = lines_by_idx or {}
    inner = ""
    if frames:
        # Group frames into runs of equal (label, text-override) so a single
        # span only spans frames that should share the same tooltip.
        def key(t):
            cell = frames[t]
            override = text_by_frame[t] if text_by_frame and t < len(text_by_frame) else None
            return (cell, override)
        i = 0
        n = min(len(frames), total_seconds)
        while i < n:
            j = i
            ki = key(i)
            while j < n and key(j) == ki:
                j += 1
            pct = (j - i) / total_seconds * 100
            cell, override = ki
            if cell is None:
                inner += (
                    f'<span class="seg seg-null" style="width:{pct:.3f}%" '
                    f'data-range="{i}–{j}s"></span>'
                )
            elif cell == NO_MATCH:
                tip = override or "(pred unresolved — not in GT shabad)"
                inner += (
                    f'<span class="seg seg-nomatch" '
                    f'data-line="—" '
                    f'data-text="{html.escape(tip)}" '
                    f'data-range="{i}–{j}s" '
                    f'style="width:{pct:.3f}%;background:{color_for_line(cell)}"></span>'
                )
            else:
                line_text = override or lines_by_idx.get(cell) or f"(line {cell})"
                inner += (
                    f'<span class="seg" data-line="{cell}" '
                    f'data-text="{html.escape(line_text)}" '
                    f'data-range="{i}–{j}s" '
                    f'style="width:{pct:.3f}%;background:{color_for_line(cell)}"></span>'
                )
            i = j
    return f'<div class="strip {row_class}">{inner}</div>'


def render_diff_strip(gt_frames, pred_frames, result, total_seconds):
    """Render the per-frame correct/wrong/unscored strip using eval's details."""
    state = {d["t"]: ("ok" if d["correct"] else "bad") for d in result["details"]}
    uem_start = result["uem_start"]
    uem_end = result["uem_end"]
    parts = []
    i = 0
    while i < total_seconds:
        s = state.get(i)
        if i < uem_start or i >= uem_end:
            s = "nil"
        elif s is None:
            s = "nil"
        j = i + 1
        while j < total_seconds:
            sj = state.get(j)
            if j < uem_start or j >= uem_end:
                sj = "nil"
            elif sj is None:
                sj = "nil"
            if sj != s:
                break
            j += 1
        pct = (j - i) / total_seconds * 100
        parts.append(f'<span class="seg d-{s}" style="width:{pct:.3f}%"></span>')
        i = j
    return '<div class="strip diff">' + "".join(parts) + "</div>"


def render_uem_masks(gt, total):
    uem_start = int(gt.get("uem", {}).get("start", 0))
    uem_end = int(gt.get("uem", {}).get("end", total))
    uem_left_pct = uem_start / total * 100
    uem_right_pct = (total - uem_end) / total * 100
    parts = []
    if uem_left_pct > 0.1:
        parts.append(
            f'<div class="uem-mask uem-left" style="width:{uem_left_pct:.3f}%" '
            f'title="outside UEM (0–{uem_start}s) — not scored"></div>'
        )
    if uem_right_pct > 0.1:
        parts.append(
            f'<div class="uem-mask uem-right" style="width:{uem_right_pct:.3f}%" '
            f'title="outside UEM ({uem_end}s–{total}s) — not scored"></div>'
        )
    return "".join(parts)


def find_audio(audio_dir, video_id):
    if not audio_dir:
        return None
    d = pathlib.Path(audio_dir)
    for name in [f"{video_id}_16k.wav", f"{video_id}.wav", f"{video_id}.mp3",
                 f"{video_id}.m4a", f"{video_id}.mp4", f"{video_id}.webm"]:
        p = d / name
        if p.exists():
            return p
    return None


def audio_tag(audio_path, embed):
    if audio_path is None:
        return ""
    if embed:
        mime = {
            ".wav": "audio/wav", ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4", ".mp4": "audio/mp4",
            ".webm": "audio/webm",
        }.get(audio_path.suffix.lower(), "audio/wav")
        data = base64.b64encode(audio_path.read_bytes()).decode()
        return f'<audio controls preload="none" src="data:{mime};base64,{data}"></audio>'
    # Link relatively.
    return f'<audio controls preload="none" src="{html.escape(str(audio_path))}"></audio>'


def render_tile(gt, pred, audio_path, embed_audio, collar, lines_cache=None,
                audio_url=None):
    total = int(gt["total_duration"])
    gt_frames = segments_to_frames(gt["segments"], total)
    pred_frames = pred_segments_to_frames(
        pred.get("segments", []), gt, total, pred_top_level=pred,
    )
    # Per-frame pred-side display text drawn from the original segments. This
    # lets the pred strip tooltip show whatever pangti text the producer emitted
    # (e.g. `banidb_gurmukhi`) even when eval couldn't resolve it to a GT line.
    pred_text_frames: list[str | None] = [None] * total
    for seg in pred.get("segments", []):
        txt = seg.get("banidb_gurmukhi")
        if not txt:
            continue
        s = int(seg["start"])
        e = min(int(seg["end"]), total)
        for t in range(s, e):
            pred_text_frames[t] = txt
    result = score_video(gt, pred, collar=collar)
    acc = result["frame_accuracy"]

    acc_class = "good" if acc >= 70 else ("mid" if acc >= 40 else "bad")
    stem_id = html.escape(gt["video_id"])
    shabad = html.escape(str(gt.get("shabad_id", "?")))
    duration_label = f"{total // 60}:{total % 60:02d}"
    uem_start = int(gt.get("uem", {}).get("start", 0))
    uem_end = int(gt.get("uem", {}).get("end", total))
    uem_masks = render_uem_masks(gt, total)

    # Line index → canonical Gurmukhi text, for hover tooltips.
    # Not shipped with the benchmark; populated from a BaniDB cache fetched
    # by load_or_fetch_lines (or left empty under --no-fetch).
    sid = gt.get("shabad_id")
    lines_by_idx = {}
    if lines_cache and sid in lines_cache:
        lines_by_idx = lines_cache[sid]
    elif gt.get("lines"):
        lines_by_idx = {l["line_idx"]: l.get("text", "") for l in gt["lines"]}
    have_text = bool(lines_by_idx)
    empty_hint = ("hover a segment to see the line"
                  if have_text else
                  "hover a segment (BaniDB text unavailable)")

    if audio_path:
        audio_html = audio_tag(audio_path, embed_audio)
    elif audio_url:
        audio_html = f'<audio controls preload="none" src="{html.escape(audio_url)}"></audio>'
    else:
        audio_html = '<p class="no-audio">audio not provided — see README to fetch</p>'

    return f"""
<article class="tile">
  <header class="tile-head">
    <div>
      <h2>{stem_id}</h2>
      <p class="sub">shabad S{shabad} · {duration_label} · UEM {uem_start}s–{uem_end}s</p>
    </div>
    <div class="acc acc-{acc_class}">{acc:.1f}%</div>
  </header>
  <div class="rows">
    <div class="row">
      <div class="strip-label">GT</div>
      <div class="strip-wrap">{render_strip(gt_frames, total, "row-gt", lines_by_idx)}{uem_masks}</div>
    </div>
    <div class="row">
      <div class="strip-label">Pred</div>
      <div class="strip-wrap">{render_strip(pred_frames, total, "row-pred", lines_by_idx, pred_text_frames)}{uem_masks}</div>
    </div>
    <div class="row">
      <div class="strip-label">Diff</div>
      <div class="strip-wrap">{render_diff_strip(gt_frames, pred_frames, result, total)}</div>
    </div>
  </div>
  <div class="hover-panel" aria-hidden="true">
    <div class="hp-row hp-gt"><span class="hp-tag">GT</span><span class="hp-text" data-empty="{html.escape(empty_hint)}"></span></div>
    <div class="hp-row hp-pred"><span class="hp-tag">Pred</span><span class="hp-text" data-empty="—"></span></div>
  </div>
  <footer class="tile-foot">
    {audio_html}
  </footer>
</article>
"""


CSS = """
:root {
  --bg: #15171c; --panel: #1d2028; --ink: #e4e6eb; --muted: #8a8f9a;
  --ok: #6fa776; --bad: #c96a6e; --nil: rgba(255,255,255,0.06);
  --accent: #b48ead;
}
* { box-sizing: border-box; }
body {
  margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
  Helvetica, Arial, sans-serif; background: var(--bg); color: var(--ink);
}
.container { max-width: 1000px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
h1 { font-size: 1.6rem; font-weight: 600; margin: 0 0 0.3rem; }
.hdr-sub { color: var(--muted); margin: 0 0 2rem; font-size: 0.95rem; }
.hdr-sub code { background: rgba(255,255,255,0.05); padding: 1px 5px;
  border-radius: 3px; font-size: 0.85em; }
.overall {
  display: inline-flex; align-items: baseline; gap: 0.5rem;
  background: var(--panel); padding: 0.75rem 1rem; border-radius: 6px;
  margin-bottom: 2rem;
}
.overall .num { font-size: 1.4rem; font-weight: 600; color: var(--accent); }
.overall .lbl { color: var(--muted); font-size: 0.85rem; }
.tiles { display: flex; flex-direction: column; gap: 1.25rem; }
.tile { background: var(--panel); border-radius: 8px; padding: 1rem 1.25rem 0.75rem; }
.tile-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 1rem; margin-bottom: 0.75rem;
}
.tile-head h2 { font-size: 0.95rem; margin: 0; font-family: ui-monospace, monospace; }
.tile-head .sub { margin: 0.15rem 0 0; font-size: 0.8rem; color: var(--muted); }
.acc { font-size: 1.3rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.acc-good { color: var(--ok); }
.acc-mid  { color: #d2a56e; }
.acc-bad  { color: var(--bad); }

/* Rows: [label][strip-wrap]. Each strip-wrap is positioned relative so UEM
   masks are correctly aligned to the strip itself, not the whole grid. */
.rows { display: flex; flex-direction: column; gap: 3px; }
.row { display: flex; align-items: center; gap: 0.5rem; }
.strip-label {
  width: 2.5rem; flex-shrink: 0;
  font-size: 0.7rem; color: var(--muted); text-align: right;
  font-family: ui-monospace, monospace;
}
.strip-wrap { position: relative; flex: 1 1 auto; min-width: 0; }
.strip {
  height: 18px; border-radius: 2px; overflow: hidden; display: flex;
  background: repeating-linear-gradient(45deg,
    rgba(255,255,255,0.02) 0 6px, rgba(255,255,255,0.04) 6px 12px);
}
.strip.diff { height: 6px; border-radius: 1px; }
.strip .seg { display: block; height: 100%; transition: opacity 0.12s; }
.strip .seg.seg-null { background: transparent; }
.strip.diff .seg.d-ok  { background: var(--ok); }
.strip.diff .seg.d-bad { background: var(--bad); }
.strip.diff .seg.d-nil { background: var(--nil); }

/* UEM overlay — lives inside strip-wrap, so never misaligned. */
.uem-mask {
  position: absolute; top: 0; bottom: 0;
  background: rgba(10,11,14,0.7);
  pointer-events: none;
  border-right: 1px dashed rgba(255,255,255,0.18);
}
.uem-mask.uem-left  { left: 0; }
.uem-mask.uem-right { right: 0; border-right: none; border-left: 1px dashed rgba(255,255,255,0.18); }

/* Hover-highlight: dim all segments except those matching the hovered line */
.tile.hover-line .strip .seg[data-line] { opacity: 0.18; }
.tile.hover-line .strip .seg.hl-match   { opacity: 1; outline: 1px solid rgba(255,255,255,0.5); outline-offset: -1px; }

/* Hover text panel — two rows, always present, populates on hover */
.hover-panel {
  margin-top: 0.6rem;
  padding: 0.5rem 0.6rem;
  background: rgba(255,255,255,0.02);
  border-radius: 4px;
  font-size: 0.9rem;
  line-height: 1.4;
  min-height: 2.6rem;
}
.hp-row { display: flex; gap: 0.5rem; align-items: baseline; }
.hp-tag {
  width: 2.5rem; flex-shrink: 0;
  font-size: 0.7rem; color: var(--muted);
  font-family: ui-monospace, monospace; text-align: right;
  padding-top: 0.15rem;
}
.hp-gt   .hp-tag { color: #6c8cb5; }
.hp-pred .hp-tag { color: #b48ead; }
.hp-text { flex: 1 1 auto; color: var(--ink); }
.hp-text:empty::before { content: attr(data-empty); color: var(--muted); font-style: italic; }
.hp-text.diff-wrong { color: var(--bad); }
.hp-text.diff-ok    { color: var(--ok); }

.tile-foot { margin-top: 0.75rem; padding-top: 0.5rem; border-top: 1px solid rgba(255,255,255,0.05); }
.tile-foot audio { width: 100%; height: 32px; }
.no-audio { color: var(--muted); font-size: 0.8rem; margin: 0; }
footer.page {
  margin-top: 3rem; color: var(--muted); font-size: 0.8rem; text-align: center;
}
footer.page a { color: var(--accent); }
"""

HOVER_JS = """
<script>
document.querySelectorAll('.tile').forEach(tile => {
  const panel = tile.querySelector('.hover-panel');
  const gtText = panel && panel.querySelector('.hp-gt .hp-text');
  const prdText = panel && panel.querySelector('.hp-pred .hp-text');
  const gtStrip = tile.querySelector('.strip.row-gt');
  const prdStrip = tile.querySelector('.strip.row-pred');

  function segAt(strip, clientX) {
    if (!strip) return null;
    const rect = strip.getBoundingClientRect();
    if (clientX < rect.left || clientX > rect.right) return null;
    // Return the .seg whose horizontal bounds contain clientX.
    for (const seg of strip.children) {
      const r = seg.getBoundingClientRect();
      if (clientX >= r.left && clientX <= r.right) return seg;
    }
    return null;
  }

  function clearHighlights() {
    tile.classList.remove('hover-line');
    tile.querySelectorAll('.strip .seg.hl-match').forEach(m => m.classList.remove('hl-match'));
  }

  function setPanel(el, seg) {
    if (!el) return;
    if (!seg || !seg.dataset || !seg.dataset.line) {
      el.textContent = '';
      el.classList.remove('diff-ok', 'diff-wrong');
      return;
    }
    el.textContent = seg.dataset.text || ('line ' + seg.dataset.line);
  }

  tile.addEventListener('mousemove', (e) => {
    const gtSeg = segAt(gtStrip, e.clientX);
    const prdSeg = segAt(prdStrip, e.clientX);
    setPanel(gtText, gtSeg);
    setPanel(prdText, prdSeg);

    clearHighlights();
    const activeSeg = (e.target.closest('.strip .seg') && e.target.closest('.strip .seg').dataset.line)
      ? e.target.closest('.strip .seg') : (gtSeg || prdSeg);
    if (activeSeg && activeSeg.dataset && activeSeg.dataset.line) {
      const line = activeSeg.dataset.line;
      tile.classList.add('hover-line');
      tile.querySelectorAll('.strip .seg[data-line="' + line + '"]').forEach(m => m.classList.add('hl-match'));
    }

    // Color GT/Pred text based on agreement at this moment.
    if (gtText && prdText) {
      const g = gtSeg && gtSeg.dataset.line;
      const p = prdSeg && prdSeg.dataset.line;
      gtText.classList.remove('diff-ok', 'diff-wrong');
      prdText.classList.remove('diff-ok', 'diff-wrong');
      if (g && p) {
        const cls = (g === p) ? 'diff-ok' : 'diff-wrong';
        gtText.classList.add(cls);
        prdText.classList.add(cls);
      }
    }
  });

  tile.addEventListener('mouseleave', () => {
    clearHighlights();
    if (gtText)  { gtText.textContent = '';  gtText.classList.remove('diff-ok','diff-wrong'); }
    if (prdText) { prdText.textContent = ''; prdText.classList.remove('diff-ok','diff-wrong'); }
  });
});
</script>
"""


def main():
    ap = argparse.ArgumentParser(description="Render benchmark submission as HTML tiles.")
    ap.add_argument("--pred", required=True, help="Submission directory")
    ap.add_argument("--gt", required=True, help="Ground-truth directory")
    ap.add_argument("--out", default="tiles.html", help="Output HTML path")
    ap.add_argument("--audio-dir", help="Optional directory with {video_id}{_16k}.wav|.mp3")
    ap.add_argument("--audio-url-template",
                    help="URL template for remote audio, e.g. "
                         "'https://example.com/audio/{video_id}.webm'. "
                         "Used when --audio-dir is not set.")
    ap.add_argument("--embed-audio", action="store_true",
                    help="Base64-embed audio into HTML (larger file, self-contained)")
    ap.add_argument("--collar", type=int, default=1, help="Scoring collar (default 1s)")
    ap.add_argument("--lines-cache", default=".banidb_cache.json", help=(
        "Path to local cache of shabad line text fetched from BaniDB. "
        "Any shabad_ids in the GT not yet in this file are fetched from "
        f"{BANIDB_API} on first run, then reused offline. "
        "Used only to enrich the hover tooltip — scoring never needs it."))
    ap.add_argument("--no-fetch", action="store_true",
                    help="Don't hit BaniDB; render with whatever is in --lines-cache (or nothing).")
    ap.add_argument("--title", default="Benchmark submission",
                    help="Title for the page")
    args = ap.parse_args()

    gt_dir = pathlib.Path(args.gt)
    pred_dir = pathlib.Path(args.pred)
    if not gt_dir.is_dir() or not pred_dir.is_dir():
        print("ERROR: --gt and --pred must both be directories", file=sys.stderr)
        sys.exit(1)

    gt_files = {f.stem: f for f in gt_dir.glob("*.json")}
    pred_files = {f.stem: f for f in pred_dir.glob("*.json")}
    common = sorted(set(gt_files) & set(pred_files))
    if not common:
        print("ERROR: no matching filenames between --pred and --gt", file=sys.stderr)
        sys.exit(1)

    # Pre-load all GT docs so we can batch any BaniDB fetches up-front.
    gt_docs = {stem: json.loads(gt_files[stem].read_text()) for stem in common}
    cache_path = pathlib.Path(args.lines_cache) if args.lines_cache else None
    lines_cache = load_or_fetch_lines(
        gt_docs.values(), cache_path, do_fetch=not args.no_fetch)

    tiles_html = []
    total_correct = 0
    total_frames = 0
    for stem in common:
        gt = gt_docs[stem]
        pred = json.loads(pred_files[stem].read_text())
        audio_path = find_audio(args.audio_dir, gt["video_id"])
        audio_url = (args.audio_url_template.format(video_id=gt["video_id"])
                     if args.audio_url_template and not audio_path else None)
        tiles_html.append(render_tile(gt, pred, audio_path, args.embed_audio, args.collar, lines_cache,
                                      audio_url=audio_url))
        r = score_video(gt, pred, collar=args.collar)
        total_correct += r["correct"]
        total_frames += r["total"]

    overall = (total_correct / total_frames * 100) if total_frames else 0.0

    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>{html.escape(args.title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{CSS}</style>
</head><body>
<div class="container">
  <h1>{html.escape(args.title)}</h1>
  <p class="hdr-sub">
    Live Captioning for Gurbani Kirtan benchmark ·
    pred = <code>{html.escape(str(pred_dir))}</code> ·
    gt = <code>{html.escape(str(gt_dir))}</code> ·
    collar = {args.collar}s
  </p>
  <div class="overall">
    <span class="num">{overall:.1f}%</span>
    <span class="lbl">overall frame accuracy ({total_correct}/{total_frames} frames over {len(common)} cases)</span>
  </div>
  <div class="tiles">
    {''.join(tiles_html)}
  </div>
  <footer class="page">
    Rendered by <code>visualize.py</code>.
    Grey band = outside UEM (unscored).
    Diff row: green = correct, red = wrong, faint = unscored.
    Hover anywhere over a strip to see the canonical line at that moment
    (GT vs Pred — red when they disagree).
  </footer>
</div>
{HOVER_JS}
</body></html>
"""

    out = pathlib.Path(args.out)
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(common)} tiles, overall {overall:.1f}%)")


if __name__ == "__main__":
    main()
