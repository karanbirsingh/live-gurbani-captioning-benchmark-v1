#!/usr/bin/env python3
"""
Render benchmark cards JSON as an interactive HTML tile page.

Usage:
    # Step 1: score and produce cards JSON
    python eval.py --pred predictions/ --gt test/ --cards cards.json

    # Step 2: render HTML
    python visualize.py cards.json --out tiles.html
    python visualize.py cards.json --out tiles.html --title "my model v2"
    python visualize.py cards.json --out tiles.html \
        --audio-url-template "https://example.com/audio/{audio_id}.webm"

The cards JSON is produced by ``eval.py --cards`` and contains everything
the renderer needs: segments, diff runs, line text, scores.  This script
is a thin wrapper that injects the JSON into an HTML template whose
CSS / JS / DOM are identical to karanbirsingh.com/gurbani-captioning,
so the visualisation looks and behaves the same in both places.

Standard library only.  No deps.
"""

import argparse
import html as html_mod
import json
import sys
from pathlib import Path

# ─── HTML shell ──────────────────────────────────────────────────────────

_FONTS_CSS = """@font-face {
                font-family: 'Mukta Mahee';
                font-style: normal;
                font-weight: 400;
                font-display: swap;
                src: url('fonts/mukta-mahee-400-gurmukhi.woff2') format('woff2');
                unicode-range: U+0951-0952, U+0964-0965, U+0A01-0A76, U+200C-200D, U+20B9, U+25CC, U+262C, U+A830-A839;
              }
              @font-face {
                font-family: 'Mukta Mahee';
                font-style: normal;
                font-weight: 600;
                font-display: swap;
                src: url('fonts/mukta-mahee-600-gurmukhi.woff2') format('woff2');
                unicode-range: U+0951-0952, U+0964-0965, U+0A01-0A76, U+200C-200D, U+20B9, U+25CC, U+262C, U+A830-A839;
              }"""

_CARD_CSS = """.cards { display: flex; flex-direction: column; gap: 1rem; }
  .card { background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 10px; padding: 1rem 1.1rem 1.1rem;
    display: flex; flex-direction: column; gap: 0.6rem; }
  .card.cold { display: none; }
  .baseline-tiles.show-cold .card.cold { display: flex; }

  .card-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; }
  .card-title { font-family: "Mukta Mahee", sans-serif; font-size: 1.15rem;
    font-weight: 600; color: var(--text); line-height: 1.25; }
  .card-meta { color: var(--text-muted); font-size: 0.8rem;
    font-variant-numeric: tabular-nums; margin-top: 0.1rem; }
  .variant-pill { display: inline-block; margin-left: 0.4rem; font-size: 0.68rem;
    letter-spacing: 0.06em; text-transform: uppercase; color: #d29922;
    border: 1px solid rgba(210,153,34,0.4); border-radius: 3px;
    padding: 0 0.35rem; vertical-align: 2px; }
  .card-acc { font-size: 0.78rem; font-weight: 600; font-variant-numeric: tabular-nums;
    color: #d5b4cf; background: rgba(180, 142, 173, 0.14);
    border: 1px solid rgba(180, 142, 173, 0.35); padding: 0.2rem 0.55rem;
    border-radius: 999px; white-space: nowrap; }
  .card-acc .card-acc-lbl { color: var(--text-muted); font-weight: 500; margin-right: 0.3rem; }

  .card-line { background: var(--bg-card-hi); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.5rem 0.7rem; display: grid;
    grid-template-columns: 3rem 1fr auto; gap: 0.4rem 0.7rem; align-items: baseline; }
  .cl-label { font-size: 0.7rem; color: var(--text-muted); letter-spacing: 0.06em;
    text-transform: uppercase; font-variant-numeric: tabular-nums; }
  .cl-text { font-family: "Mukta Mahee", sans-serif; font-size: 1.05rem;
    line-height: 1.3; min-height: 2.73rem; display: flex; align-items: center; }
  .cl-text.muted { color: var(--text-muted); font-style: italic;
    font-family: inherit; font-size: 0.9rem; min-height: 2.73rem; }
  .cl-status { font-size: 0.95rem; font-weight: 600;
    font-family: "Helvetica Neue", Arial, sans-serif;
    min-width: 1rem; text-align: right; color: var(--text-muted);
    font-variant-numeric: tabular-nums; line-height: 1.3; }
  .cl-row.pred.ok  .cl-status { color: #6fa776; }
  .cl-row.pred.bad .cl-status { color: #c96a6e; }
  .cl-row.pred.nil .cl-status { color: var(--text-muted); }
  .cl-row.gt .cl-status { visibility: hidden; }
  .cl-row { display: contents; }

  audio.card-audio { width: 100%; height: 36px; color-scheme: dark; }

  .card-timeline { position: relative; width: 100%; user-select: none;
    cursor: pointer; touch-action: none; outline: none;
    padding: 4px 0 4px 3.2rem; border-radius: 4px; }
  .card-timeline:focus-visible { box-shadow: 0 0 0 2px var(--accent-dim); }
  .timeline-labels { position: absolute; left: 0; top: 4px; width: 3rem;
    display: flex; flex-direction: column; pointer-events: none;
    font-size: 0.7rem; color: var(--text-muted); }
  .timeline-labels span { height: 14px; line-height: 14px; margin-bottom: 4px;
    text-align: right; padding-right: 0.5rem; letter-spacing: 0.02em; }
  .timeline-wrap { position: relative; width: 100%; }
  .timeline-row { position: relative; height: 14px;
    background: rgba(255, 255, 255, 0.035);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 4px; overflow: hidden; margin-bottom: 4px; }
  .timeline-row:last-of-type { margin-bottom: 0; }
  .timeline-row .seg { position: absolute; top: 0; bottom: 0; background: var(--accent); }
  .timeline-row.row-gt .seg, .timeline-row.row-pred .seg {
    border-right: 1px solid rgba(13, 17, 23, 0.55); }
  .timeline-row.row-gt .seg:last-child, .timeline-row.row-pred .seg:last-child { border-right: none; }
  .timeline-row.row-gt .seg   { background: #6c8cb5; }
  .timeline-row.row-pred .seg { background: #b48ead; }
  .timeline-row.row-diff { height: 4px; background: transparent; border: none;
    border-radius: 2px; margin-top: 4px; margin-bottom: 0; overflow: visible; }
  .timeline-row.row-diff .seg { top: 0; bottom: 0; border-radius: 2px; }
  .timeline-row.row-diff .seg.ok  { background: #6fa776; }
  .timeline-row.row-diff .seg.bad { background: #c96a6e; }
  .timeline-row.row-diff .seg.nil { background: rgba(255, 255, 255, 0.06); }

  .uem-mask { position: absolute; top: 0; bottom: 0; background: rgba(13,17,23,0.72);
    pointer-events: none; z-index: 2; }
  .uem-mask.left  { border-right: 1px dashed rgba(210,153,34,0.4); }
  .uem-mask.right { border-left:  1px dashed rgba(210,153,34,0.4); }

  .timeline-wrap[class*=" highlight-line"] .row-gt .seg,
  .timeline-wrap[class^="highlight-line"]  .row-gt .seg,
  .timeline-wrap[class*=" highlight-line"] .row-pred .seg,
  .timeline-wrap[class^="highlight-line"]  .row-pred .seg {
    opacity: 0.28; transition: opacity 0.08s ease-out; }
  .timeline-wrap.highlight-active .row-gt .seg.seg-active,
  .timeline-wrap.highlight-active .row-pred .seg.seg-active {
    opacity: 1; box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.5) inset; }

  .seg-tip { position: absolute; z-index: 20; pointer-events: none;
    background: rgba(22, 27, 34, 0.96); backdrop-filter: blur(8px);
    color: var(--text); border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 6px; padding: 0.4rem 0.55rem; font-size: 0.8rem;
    line-height: 1.35; max-width: 24rem;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45), 0 1px 2px rgba(0, 0, 0, 0.5);
    opacity: 0; transform: translateY(2px);
    transition: opacity 0.08s ease-out, transform 0.08s ease-out; white-space: normal; }
  .seg-tip.visible { opacity: 1; transform: translateY(0); }
  .seg-tip .tip-meta { font-size: 0.68rem; color: var(--text-muted);
    letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 0.15rem;
    font-variant-numeric: tabular-nums; }
  .seg-tip .tip-meta .tip-row-label.gt   { color: #a8bcd9; }
  .seg-tip .tip-meta .tip-row-label.pred { color: #d5b4cf; }
  .seg-tip .tip-text { font-family: "Mukta Mahee", sans-serif; font-size: 0.92rem; }
  .seg-tip .tip-count { margin-top: 0.25rem; font-size: 0.72rem; color: var(--text-muted); }

  .playhead { position: absolute; top: 0; bottom: 0; width: 2px;
    background: var(--text); border-radius: 1px; transform: translateX(-1px);
    left: 0%; pointer-events: none; z-index: 3; }
  .playhead::after { content: ""; position: absolute; top: -2px; left: 50%;
    width: 10px; height: 10px; border-radius: 50%; background: var(--text);
    transform: translate(-50%, 0); border: 2px solid var(--bg);
    box-shadow: 0 0 0 1px var(--text); }"""

_PAGE_CSS = """
:root {
  --bg: #0d1117; --bg-card: #161b22; --bg-card-hi: #1c2333;
  --text: #e6edf3; --text-muted: #8b949e;
  --border: #30363d; --accent: #b48ead; --accent-dim: rgba(180,142,173,0.35);
}
* { box-sizing: border-box; margin: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.5;
}
.container { max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 0.2rem; }
.subtitle { color: var(--text-muted); font-size: 0.9rem; margin-bottom: 1.5rem; }
.subtitle code { background: rgba(255,255,255,0.06); padding: 1px 5px;
  border-radius: 3px; font-size: 0.85em; }
.summary { font-size: 0.95rem; margin-bottom: 1rem; }
.summary b { color: var(--accent); }
.toggle { font-size: 0.85rem; color: var(--text-muted); cursor: pointer;
  user-select: none; margin-left: 1rem; }
.toolbar { display: flex; align-items: baseline; flex-wrap: wrap;
  gap: 0.5rem; margin-bottom: 0.5rem; }
.cold-note { font-size: 0.82rem; color: var(--text-muted); margin-bottom: 1rem;
  display: none; }
.show-cold ~ .cold-note, .container.show-cold .cold-note { display: block; }
footer.page { margin-top: 2.5rem; color: var(--text-muted); font-size: 0.8rem;
  text-align: center; }
footer.page a { color: var(--accent); }
"""

_CARD_TEMPLATE = """<template id="card-template">
  <div class="card">
    <div class="card-line">
      <div class="cl-row gt">
        <span class="cl-label">Actual</span>
        <span class="cl-text muted">—</span>
        <span class="cl-status"></span>
      </div>
      <div class="cl-row pred">
        <span class="cl-label">Guess</span>
        <span class="cl-text muted">—</span>
        <span class="cl-status"></span>
      </div>
    </div>
    <div class="card-head">
      <div>
        <div class="card-title"></div>
        <div class="card-meta"></div>
      </div>
      <div class="card-acc"><span class="card-acc-lbl">accuracy</span><span class="card-acc-val">—</span></div>
    </div>
    <audio class="card-audio" controls preload="none"></audio>
    <div class="card-timeline" role="slider" tabindex="0" aria-label="Seek"
         aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
      <div class="timeline-labels"><span>Actual</span><span>Guess</span></div>
      <div class="timeline-wrap">
        <div class="timeline-row row-gt"></div>
        <div class="timeline-row row-pred"></div>
        <div class="timeline-row row-diff"></div>
        <div class="playhead"></div>
        <div class="seg-tip" role="tooltip" aria-hidden="true"></div>
      </div>
    </div>
  </div>
</template>"""

_CARD_JS = """function fmtTime(t) {
  if (!isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t/60), s = Math.floor(t%60);
  return `${m}:${s.toString().padStart(2,"0")}`;
}
function lineAtTime(segments, t) {
  let w = null;
  for (const s of segments) { if (t >= s.start && t < s.end) w = s.line_idx; }
  return w;
}
function fillRow(rowEl, segs, duration, classFor, lineTextFor) {
  if (duration <= 0) return;
  for (const s of segs) {
    const left = Math.max(0, s.start)/duration*100;
    const width = Math.max(0, s.end - s.start)/duration*100;
    if (width <= 0) continue;
    const el = document.createElement("div");
    el.className = "seg " + (classFor ? classFor(s) : "");
    el.style.left = left + "%";
    el.style.width = width + "%";
    el.dataset.start = s.start;
    el.dataset.end = s.end;
    if (s.line_idx != null) {
      el.dataset.lineIdx = s.line_idx;
      const text = lineTextFor ? lineTextFor(s.line_idx) : null;
      if (text) el.dataset.lineText = text;
    } else if (s.state) {
      el.dataset.state = s.state;
    }
    rowEl.appendChild(el);
  }
}

function makeCard(data) {
  const frag = document.getElementById("card-template").content.cloneNode(true);
  const card = frag.querySelector(".card");
  if (data.variant && data.variant !== "normal") card.classList.add("cold");

  card.querySelector(".card-title").textContent = data.title;
  const meta = card.querySelector(".card-meta");
  meta.textContent = `Shabad ${data.shabad_id} · ${Math.round(data.duration/6)/10} min · ${data.lines.length} lines`;
  if (data.variant && data.variant !== "normal") {
    const p = document.createElement("span");
    p.className = "variant-pill";
    p.textContent = `cold-start ${data.variant === "cold33" ? "33%" : "66%"}`;
    meta.appendChild(p);
  }
  card.querySelector(".card-acc-val").textContent = `${data.score.frame_accuracy.toFixed(1)}%`;

  const timeline = card.querySelector(".card-timeline");
  const wrap = card.querySelector(".timeline-wrap");
  const playhead = card.querySelector(".playhead");
  const rowGt = card.querySelector(".row-gt");
  const rowPred = card.querySelector(".row-pred");
  const rowDiff = card.querySelector(".row-diff");
  const audio = card.querySelector(".card-audio");
  audio.src = `https://github.com/karanbirsingh/bin/raw/main/audio/${data.audio_id}.webm`;

  const lineMap = Object.fromEntries((data.lines||[]).map(l => [l.line_idx, l.text]));
  const lineTextFor = idx => lineMap[idx];
  fillRow(rowGt, data.gt_segments, data.duration, null, lineTextFor);
  fillRow(rowPred, data.pred_segments, data.duration, null, lineTextFor);
  fillRow(rowDiff, data.diff_runs, data.duration, s => s.state);

  // UEM mask for cold-start: dim unscored prefix/suffix.
  if (data.uem && (data.uem.start > 0.01 || data.uem.end < data.duration - 0.01)) {
    if (data.uem.start > 0.01) {
      const m = document.createElement("div");
      m.className = "uem-mask left";
      m.style.left = "0%";
      m.style.width = (data.uem.start/data.duration*100) + "%";
      wrap.appendChild(m);
    }
    if (data.uem.end < data.duration - 0.01) {
      const m = document.createElement("div");
      m.className = "uem-mask right";
      const l = data.uem.end/data.duration*100;
      m.style.left = l + "%"; m.style.width = (100-l) + "%";
      wrap.appendChild(m);
    }
  }

  // Hover tooltip + cross-row highlight.
  const tip = wrap.querySelector(".seg-tip");
  function showTip(segEl) {
    const lineIdx = segEl.dataset.lineIdx;
    const inGt = segEl.parentElement.classList.contains("row-gt");
    const rowLabel = inGt ? "Actual" : "Guess";
    const rowClass = inGt ? "gt" : "pred";
    const start = parseFloat(segEl.dataset.start);
    const end = parseFloat(segEl.dataset.end);
    const range = `${fmtTime(start)}–${fmtTime(end)}`;
    const text = segEl.dataset.lineText || "";

    let gtCount = 0, predCount = 0;
    if (lineIdx != null) {
      gtCount = wrap.querySelectorAll(`.row-gt .seg[data-line-idx="${lineIdx}"]`).length;
      predCount = wrap.querySelectorAll(`.row-pred .seg[data-line-idx="${lineIdx}"]`).length;
    }
    tip.innerHTML = "";
    const m = document.createElement("div"); m.className = "tip-meta";
    const lbl = document.createElement("span");
    lbl.className = `tip-row-label ${rowClass}`;
    lbl.textContent = rowLabel;
    m.appendChild(lbl);
    m.appendChild(document.createTextNode(
      lineIdx != null ? `  ·  line ${lineIdx}  ·  ${range}` : `  ·  ${range}`));
    tip.appendChild(m);
    if (text) {
      const t = document.createElement("div");
      t.className = "tip-text"; t.textContent = text; tip.appendChild(t);
    }
    if (lineIdx != null && (gtCount + predCount) > 1) {
      const c = document.createElement("div");
      c.className = "tip-count";
      c.textContent = `Appears ${gtCount}× in actual · ${predCount}× in guess`;
      tip.appendChild(c);
    }

    const hostRect = wrap.getBoundingClientRect();
    const segRect = segEl.getBoundingClientRect();
    tip.style.visibility = "hidden";
    tip.classList.add("visible");
    const tipW = tip.offsetWidth, tipH = tip.offsetHeight;
    let x = segRect.left - hostRect.left + segRect.width/2 - tipW/2;
    x = Math.max(2, Math.min(x, hostRect.width - tipW - 2));
    tip.style.left = `${x}px`;
    if (hostRect.top >= tipH + 8) tip.style.top = `${-tipH - 6}px`;
    else tip.style.top = `${hostRect.height + 6}px`;
    tip.style.visibility = "";
    tip.setAttribute("aria-hidden", "false");

    wrap.querySelectorAll(".seg.seg-active").forEach(el => el.classList.remove("seg-active"));
    if (lineIdx != null) {
      wrap.classList.add("highlight-line", "highlight-active");
      wrap.querySelectorAll(
        `.row-gt .seg[data-line-idx="${lineIdx}"], .row-pred .seg[data-line-idx="${lineIdx}"]`
      ).forEach(el => el.classList.add("seg-active"));
    }
  }
  function hideTip() {
    tip.classList.remove("visible");
    tip.setAttribute("aria-hidden", "true");
    wrap.classList.remove("highlight-line", "highlight-active");
    wrap.querySelectorAll(".seg.seg-active").forEach(el => el.classList.remove("seg-active"));
  }
  wrap.addEventListener("pointerover", ev => {
    const seg = ev.target.closest(".seg");
    if (!seg) return;
    const row = seg.parentElement;
    if (!row.classList.contains("row-gt") && !row.classList.contains("row-pred")) return;
    showTip(seg);
  });
  wrap.addEventListener("pointerout", ev => {
    const seg = ev.target.closest(".seg");
    if (!seg) return;
    const to = ev.relatedTarget;
    if (to && (to === tip || tip.contains(to) || seg.contains(to))) return;
    hideTip();
  });
  wrap.addEventListener("pointerleave", hideTip);
  timeline.addEventListener("pointerdown", hideTip);

  // Now-playing panel + playhead sync.
  const gtRow = card.querySelector(".cl-row.gt");
  const predRow = card.querySelector(".cl-row.pred");
  const gtText = gtRow.querySelector(".cl-text");
  const predText = predRow.querySelector(".cl-text");
  const predStatus = predRow.querySelector(".cl-status");
  function setSlot(textEl, idx) {
    if (idx == null) { textEl.classList.add("muted");
      textEl.style.fontFamily = "inherit"; textEl.textContent = "—"; return; }
    textEl.classList.remove("muted");
    textEl.style.fontFamily = '"Mukta Mahee", sans-serif';
    textEl.textContent = lineMap[idx] || `(line ${idx})`;
  }
  function diffStateAt(t) {
    for (const r of data.diff_runs) { if (t >= r.start && t < r.end) return r.state; }
    return null;
  }
  function render(t) {
    const pct = data.duration > 0 ? (t/data.duration)*100 : 0;
    playhead.style.left = pct + "%";
    timeline.setAttribute("aria-valuenow", pct.toFixed(1));
    timeline.setAttribute("aria-valuetext", `${fmtTime(t)} of ${fmtTime(data.duration)}`);
    setSlot(gtText, lineAtTime(data.gt_segments, t));
    setSlot(predText, lineAtTime(data.pred_segments, t));
    predRow.classList.remove("ok","bad","nil");
    const st = diffStateAt(t);
    if (st === "ok") { predRow.classList.add("ok"); predStatus.textContent = "✓"; }
    else if (st === "bad") { predRow.classList.add("bad"); predStatus.textContent = "✗"; }
    else { predRow.classList.add("nil"); predStatus.textContent = ""; }
  }

  audio.addEventListener("timeupdate", () => { if (!scrubbing) render(audio.currentTime); });
  audio.addEventListener("seeked",     () => { if (!scrubbing) render(audio.currentTime); });
  audio.addEventListener("loadedmetadata", () => render(audio.currentTime));

  function timeFromClientX(x) {
    const r = wrap.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (x - r.left)/r.width));
    return ratio * data.duration;
  }
  let scrubbing = false, wasPlaying = false;
  timeline.addEventListener("pointerdown", e => {
    scrubbing = true; wasPlaying = !audio.paused;
    if (wasPlaying) audio.pause();
    try { timeline.setPointerCapture(e.pointerId); } catch(_){}
    const t = timeFromClientX(e.clientX);
    audio.currentTime = t; render(t); e.preventDefault();
  });
  timeline.addEventListener("pointermove", e => {
    if (!scrubbing) return;
    const t = timeFromClientX(e.clientX);
    audio.currentTime = t; render(t);
  });
  function endScrub(e) {
    if (!scrubbing) return;
    scrubbing = false;
    try { timeline.releasePointerCapture(e.pointerId); } catch(_){}
    render(audio.currentTime);
    if (wasPlaying) audio.play().catch(()=>{});
  }
  timeline.addEventListener("pointerup", endScrub);
  timeline.addEventListener("pointercancel", endScrub);
  timeline.addEventListener("keydown", e => {
    const step = e.shiftKey ? 10 : 5;
    let t = audio.currentTime, handled = true;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") t = Math.min(data.duration, t+step);
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") t = Math.max(0, t-step);
    else if (e.key === "Home") t = 0;
    else if (e.key === "End") t = Math.max(0, data.duration - 0.1);
    else handled = false;
    if (handled) { audio.currentTime = t; render(t); e.preventDefault(); }
  });

  render(data.uem ? data.uem.start : 0);
  return card;
}"""

_BOOT_JS = """
const cardsEl = document.getElementById("cards");
for (const c of CARDS) cardsEl.appendChild(makeCard(c));

const summary = document.getElementById("summary");
const toggle = document.getElementById("cold-toggle");
function updateSummary() {
  if (toggle.checked) {
    summary.innerHTML = `Overall accuracy: <b>${STATS.all.toFixed(1)}%</b> · `
      + `<span style="opacity:0.75">${STATS.nBase} base (<b>${STATS.base.toFixed(1)}%</b>) + `
      + `${STATS.nCold} cold-start (<b>${STATS.cold.toFixed(1)}%</b>)</span>`;
  } else {
    summary.innerHTML = `Overall accuracy on ${STATS.nBase} base recordings: <b>${STATS.base.toFixed(1)}%</b>`;
  }
}
toggle.addEventListener("change", () => {
  document.getElementById("tile-wrap").classList.toggle("show-cold", toggle.checked);
  updateSummary();
});
updateSummary();
"""


def main():
    ap = argparse.ArgumentParser(
        description="Render benchmark cards JSON as interactive HTML tiles.")
    ap.add_argument("cards", help="Cards JSON file produced by eval.py --cards")
    ap.add_argument("--out", default="tiles.html", help="Output HTML path")
    ap.add_argument("--title", default="Benchmark submission",
                    help="Page title")
    ap.add_argument("--audio-url-template",
                    help="URL template for audio, e.g. "
                         "'https://example.com/audio/{audio_id}.webm'. "
                         "{audio_id} is substituted per card.")
    args = ap.parse_args()

    data = json.loads(Path(args.cards).read_text())
    cards = data["cards"]
    stats = data.get("stats", {})

    # Inject audio URLs if template provided.
    if args.audio_url_template:
        for c in cards:
            c["audio_url"] = args.audio_url_template.format(
                audio_id=c["audio_id"], video_id=c["video_id"])

    # Compute stats for the summary bar.
    base = [c for c in cards if c.get("variant") == "normal"]
    cold = [c for c in cards if c.get("variant") != "normal"]
    def avg_acc(lst):
        return sum(c["score"]["frame_accuracy"] for c in lst) / len(lst) if lst else 0
    js_stats = {
        "all": stats.get("overall", avg_acc(cards)),
        "base": avg_acc(base),
        "cold": avg_acc(cold),
        "nBase": len(base),
        "nCold": len(cold),
    }

    cards_json = json.dumps(cards, ensure_ascii=False)
    stats_json = json.dumps(js_stats)
    title_esc = html_mod.escape(args.title)

    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>{title_esc}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_FONTS_CSS}
{_PAGE_CSS}
{_CARD_CSS}</style>
</head><body>
<div class="container">
  <h1>{title_esc}</h1>
  <p class="subtitle">
    Live Captioning for Gurbani Kirtan benchmark ·
    collar = {data.get('collar', 1)}s
  </p>
  <div class="toolbar">
    <div class="summary" id="summary"></div>
    <label class="toggle"><input type="checkbox" id="cold-toggle" /> Show cold-start copies</label>
  </div>
  <div class="cold-note">
    Cold-start copies are the same recordings started 33% and 66% of the way in,
    scored only on the remaining portion (dimmed region is unscored).
    They simulate joining the kirtan late.
  </div>
  {_CARD_TEMPLATE}
  <div id="tile-wrap" class="baseline-tiles">
    <div id="cards" class="cards"></div>
  </div>
  <footer class="page">
    Rendered by <code>visualize.py</code>.
    Hover a segment to see the line text; drag the timeline to scrub audio.
    Green = correct, red = wrong, faint = unscored.
    <br>
    <a href="https://github.com/karanbirsingh/live-gurbani-captioning-benchmark-v1">GitHub</a>
  </footer>
</div>
<script>
const CARDS = {cards_json};
const STATS = {stats_json};
{_CARD_JS}
{_BOOT_JS}
</script>
</body></html>
"""

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    n_base = len(base)
    n_cold = len(cold)
    overall = js_stats["all"]
    print(f"wrote {out} ({n_base} base + {n_cold} cold-start tiles, overall {overall:.1f}%)")


if __name__ == "__main__":
    main()
