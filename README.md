# Live Captioning for Gurbani Kirtan — Benchmark

https://github.com/user-attachments/assets/7408cd35-1e7f-4f71-ab68-49705202b2bc

*Example `visualize.py` output — GT / prediction / diff strips with live-updating Gurmukhi on hover.*

This is a small/open benchmark meant to frame the end-to-end problem of
following along with Gurbani Kirtan and to give initial experiments
something concrete to score against: given a stream, produce a causal
timeline saying which line of which shabad is being sung at each moment
— captioning, but with the allowed outputs restricted to Gurbani rather
than free-form transcription.

The restriction matters. In a Gurbani context, displaying a misspelled line (like Youtube auto-generated captions might) is not acceptable. Any system that emits raw ASR output will occasionally produce those errors.

- **Data:** 4 kirtan recordings, each evaluated from 3 start offsets → 12 cases.
- **Metric:** frame accuracy at 1s resolution with a 1s collar and
  gap-tolerant scoring.

An example experience is running live at **[bani.karanbirsingh.com](https://bani.karanbirsingh.com)**. It includes buttons to manually confirm a prediction, reset incorrect identification, etc (a good system should assist Sewadars rather than drive autonomously).

A solution for this benchmark could be leveraged in systems to do things like:
- help Sangat members identify the current Shabad in live contexts and follow along
- speed up captioning efforts of existing videos after-the-fact
- auto-index unstructured or archival Kirtan recordings

## Task

Given a stream of kirtan audio, at every moment `t` output the system's prediction for `(shabad_id, line_idx)` or `null`.

The benchmark can be used in both a "live" setting and "offline" setting. For live streaming, predictions at time `t` may only depend on audio up to `t`. For offline captioning, the full audio can be used.

The benchmark can also be used with or without Shabad knowledge:

- **Blind (primary):** your system identifies the shabad from audio alone.
- **Oracle (reference):** your system is given the ground-truth `shabad_id`
  upfront and only tracks lines. For example, a user 'confirms' the shabad.

Both are scored with the same metric against the same ground truth.

The benchmark is intentionally end-to-end. A system can go directly from model to outputs, or include deterministic code before or after ASR, etc.

## Cost and latency

The benchmark does not explicitly include system cost, but it's useful to include this as a note. For example, production ASR systems like Google Chirp produce better-looking ASR output but require ongoing cost. A local model may produce worse output requiring fuzzy matching, but can be run on CPU or edge devices for close to free.

Latency is reflected in the live benchmark variant.

## Data

4 hand-reviewed kirtan recordings from YouTube, each evaluated from 3 start
offsets (0%, 33%, 66% into the recording) to test both fresh-start and
join-mid-shabad behaviour. Total: 12 evaluation cases, ~57 minutes of scored
audio.

| Video ID | Shabad ID | Duration | Segments |
|----------|-----------|----------|----------|
| IZOsmkdmmcg | 4377 | 7.7 min | 16 |
| kZhIA8P6xWI | 1821 | 5.1 min | 19 |
| kchMJPK9Axs | 1341 | 10.9 min | 22 |
| zOtIpxMT9hU | 3712 | 4.9 min | 10 |

Cold-start variants (`_cold33`, `_cold66`) are derived from the same
recording by moving the UEM (scored region) forward by 33% or 66%. The audio
file is the same; your system just starts processing from a later offset.

### Audio

Requires [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) and `ffmpeg` on your PATH.

```bash
for id in IZOsmkdmmcg kZhIA8P6xWI kchMJPK9Axs zOtIpxMT9hU; do
  yt-dlp -x --audio-format wav -o "%(id)s.wav" "https://youtube.com/watch?v=$id"
  ffmpeg -y -i "$id.wav" -ar 16000 -ac 1 "${id}_16k.wav" && rm "$id.wav"
done
```

### Ground truth format

Each case is one JSON file under `test/`. Filenames match `{video_id}.json`
or `{video_id}_cold{33|66}.json`.

```json
{
  "video_id": "IZOsmkdmmcg",
  "shabad_id": 4377,
  "total_duration": 460.9,
  "uem": { "start": 0.0, "end": 455.9 },
  "segments": [
    { "start": 28.0, "end": 45.0, "line_idx": 1 },
    { "start": 48.0, "end": 62.0, "line_idx": 2 }
  ]
}
```

- `uem` — un-partitioned evaluation map. Only frames inside UEM are scored.
  Intros, outros, and cold-start skip regions live outside UEM.
- `segments` — authoritative labeled regions. Gaps between segments are
  scored with a looser rule (see *Scoring*).
- `line_idx` — 0-indexed within the shabad.

`shabad_id` and `line_idx` are the same identifiers used by
[BaniDB](https://banidb.com/) and SikhiToTheMax. The canonical Gurmukhi for
any `(shabad_id, line_idx)` can be looked up via their APIs. The scorer
only needs the integers; keeping predictions as IDs rather than raw
characters is intentional.

## Submitting

Two paths produce the same submission JSON that `eval.py` scores. Most
production systems for following Gurbani Kirtan already drive the
[STTM Bani Controller](https://www.sikhitothemax.org/control) protocol
to push the current line to a Gurdwara projector running STTM Desktop —
if yours is one of them, prefer that path. Offline / batch research
systems can write the JSON directly.

### Drive STTM Bani Controller (recommended)

Point your existing STTM client at `sttm_recorder.py` (a tiny fake STTM
relay) instead of `api.sikhitothemax.org`. The recorder buffers the
`shabad` events you emit and writes them out as a benchmark submission;
`eval.py` scores the result like any other submission.

```bash
pip install "python-socketio>=5,<6" "aiohttp>=3,<4"

# Terminal 1: start the recorder for one GT case.
python sttm_recorder.py \
  --video-id IZOsmkdmmcg \
  --out submission/IZOsmkdmmcg.json \
  --code bench --pin 1234 --port 5051

# Terminal 2: point your system's STTM client at the recorder.
# Use 'bench' as the sync code and 1234 as the PIN.
./my_system --stt-relay http://localhost:5051 \
            --code bench --pin 1234 \
            --audio IZOsmkdmmcg_16k.wav
```

For multi-video runs, loop the recorder over each GT case from a shell
wrapper.

**Wire format.** Mirrors STTM Bani Controller exactly (socket.io v2,
`data` events with `host` / `type` / `pin`), with one extension: every
`shabad` event must include an `audio_t` field — float seconds since
the start of the audio file your system is processing. STTM Desktop
ignores unknown fields, so the same client code drives both this
recorder and a real Gurdwara projector with no branching.

| Event | Required fields | Recorder behaviour |
|---|---|---|
| `request-control` | `pin` | Replies with `response-control`; any PIN works in the recorder |
| `shabad` | `shabadId`, `verseId`, `lineCount`, `audio_t` | Closes the previous segment at `audio_t`, opens a new one |
| `text` / `bani` / `ceremony` | `audio_t` | Closes the current segment; does **not** open a new one — the system has gone to a generic / out-of-scope screen |
| `bench-end` (recorder extension) | `audio_t` | Closes the current segment and writes the submission JSON |

Disconnecting without `bench-end` also finalizes, using the `audio_t` of
the most recent event as the end of the open segment.

A 60-line example client is at `examples/sttm_submission_example.py`.

### Write JSON directly (offline / batch)

One JSON file per GT case, same filename stem (`IZOsmkdmmcg.json`,
`IZOsmkdmmcg_cold33.json`, etc.), placed in a single directory:

```json
{
  "video_id": "IZOsmkdmmcg",
  "segments": [
    { "start": 30.0, "end": 47.0, "line_idx": 1 },
    { "start": 49.5, "end": 60.0, "line_idx": 2 }
  ]
}
```

Rules:

- `start < end`, both in seconds relative to the start of the audio file
  (not relative to UEM).
- `line_idx` is 0-indexed within the **predicted** shabad. A system
  producing correct line indices for the wrong shabad will score poorly.
- Segments may overlap; in the scorer, later segments overwrite earlier
  ones per-frame.
- Unsegmented regions are interpreted as `null` predictions. `null` is
  accepted inside GT gaps (and near segment boundaries) but counts as
  wrong inside a GT segment interior.
- Predictions outside UEM are ignored by the scorer (cost nothing).

A minimal working example that writes valid (but empty) submission files
is at `examples/minimal_submission.py`. This is also the path the
committed baselines (`baselines/empty/`, `baselines/shifted_5s/`,
`baselines/perfect/`) use.

## Scoring

The scorer discretises time to 1-second frames and scores every frame
inside UEM. Each frame falls into one of three regions, each with its own
accepted-prediction set:

| Region | Definition | Accepted predictions |
|---|---|---|
| Segment interior | Inside a labeled `(start, end, line_idx)`, not within `collar` of an edge | Exact `line_idx` only |
| Collar | Within `collar` seconds of a segment boundary | Exact line, adjacent line, or `null` |
| Gap | Between two consecutive segments, outside their collars | Line before the gap, line after the gap, or `null` |

Frames outside any segment and outside any gap (e.g. before the first
segment, after the last) are considered unscored — anything is accepted.

The primary metric is **frame accuracy at `collar=1s`**. This is the
number reported alongside any result from this benchmark.

```bash
python eval.py --pred my_submission/ --gt test/ --collar 1
```

The collar is deliberately tight (1s, not the 2s sometimes seen in speaker
diarization).

`eval.py` is standard-library Python 3.10+ only — no dependencies.

## Visualizing a submission

`visualize.py` renders a submission as a single self-contained HTML file

```bash
# Without audio (the strips still work)
python visualize.py --pred baselines/perfect/ --gt test/ --out tiles.html

# With audio (fetch the WAVs first; see Audio section above)
python visualize.py --pred my_submission/ --gt test/ \
  --audio-dir audio/ --out tiles.html --title "my model v2"

# Fully self-contained (base64-embeds audio; larger file)
python visualize.py --pred my_submission/ --gt test/ \
  --audio-dir audio/ --embed-audio --out tiles.html
```

Open `tiles.html` in any browser.

**Hover tooltip** shows the canonical Gurmukhi line for GT and Pred at the
cursor position, green when they agree and red when they disagree. The
line text is not shipped with this repo — on first run `visualize.py`
fetches it from the public BaniDB API
(`api.banidb.com/v2/shabads/{shabad_id}`) for each `shabad_id` in the GT
and caches the result to `.banidb_cache.json` (gitignored). Subsequent
runs are fully offline. Pass `--no-fetch` to skip the network call.

## Baselines

Three reference points are committed under `baselines/`:

| Submission | Description | Frame accuracy |
|---|---|---|
| `baselines/empty/` | No segments — `null` everywhere | **26.0%** |
| `baselines/shifted_5s/` | Ground truth, every segment delayed by 5 seconds | **85.5%** |
| `baselines/perfect/` | Copy of ground truth | **100.0%** |

The empty baseline is non-trivially above 0% because gaps accept `null` as
a correct prediction, so silence during non-singing portions is scored
correctly. It's mostly useful as a sanity check on the scorer itself.

The shifted baseline approximates the latency profile of a real online
ASR pipeline that lags audio by a few seconds. The 1s collar absorbs a
small fraction; the rest of the cost shows up inside segment interiors.
Regenerate it (or other shift values) with::

    python examples/make_shifted_baseline.py --shift 5

Confidently emitting the *wrong* shabad scores worse than emitting
nothing — see ``examples/make_shifted_baseline.py`` for how to construct
that case and others.

Verify with:

```bash
python eval.py --pred baselines/empty/      --gt test/
python eval.py --pred baselines/shifted_5s/ --gt test/
python eval.py --pred baselines/perfect/    --gt test/
```

Model-specific numbers are deliberately kept out of this README so it
stays model-agnostic.

## Why not WER?

WER and CER are the natural metrics for a transcription task. This
benchmark evaluates a different task — which line is currently on screen. Misspelled Gurmukhi in a religious context is not a neutral error. Framing the end-to-end task as a snap-to-canonical captioning problem forces systems to either output real Gurbani or output nothing.

This benchmark measures the thing the product shows. It rewards any
combination of acoustic modelling, retrieval, and decoding that ends with
the right line highlighted at the right time.

## Limitations

- only four manually-verified recordings
- simple cases for now: no intermediate Katha, Simran, or interludes to other Shabads
- each audio file contains one Shabad (no transitions)

## Contributing

Feel free to get in touch if you would like to contribute more tracks to
the benchmark, or if you're using it for your own experiments and want to
compare notes.

## References

- Canonical text: [BaniDB](https://api.banidb.com) and SikhiToTheMax, using
  the same `shabad_id` as both.
- Reference implementation: [bani.karanbirsingh.com](https://bani.karanbirsingh.com)

## License

Code (`eval.py`, `visualize.py`, `examples/`) is MIT. See `LICENSE`.

Ground-truth annotations under `test/` and `baselines/` are released under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribution:
"Live Captioning for Gurbani Kirtan benchmark v1."
