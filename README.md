# cutlist

> Turn free-form editor's notes into frame-accurate, importable change lists.

`cutlist` is an **editor's-notes router**. Feed it the prose a director or editor
writes during review — plus, optionally, a Frame.io comment export — and it
extracts discrete, **timecode-anchored change requests** (action, target
timecode or range, rationale, confidence) and exports them to two studio-standard
interchange formats:

- **OpenTimelineIO** (`.otio`) — markers/clips on a timeline, round-trip safe.
- **CMX3600 EDL** (`.edl`) — parses cleanly in any NLE.

The structured extraction runs on the [`replykit`](https://github.com/edgarh92/replykit)
agent engine; timecode math is deterministic and frame-rate aware.

## Why it's different (for studios)

Review notes are where post-production loses time. A director's prose ("punch in
around the one-minute mark, the wide feels cold") has to be read, interpreted,
and hand-transcribed into the NLE — per note, per round, per editor. `cutlist`
closes that loop, and it's built for a **studio** workflow specifically:

- **Frame-accurate, not "AI-approximate".** The model never does timecode math.
  Every `HH:MM:SS:FF` is parsed **deterministically** against the project frame
  rate, so `01:00:05:00` at 24fps is always frame 86520 — reproducible, auditable,
  and identical run-to-run. The LLM only decides *what* the note means, never
  *where* it lands.
- **It speaks the formats your pipeline already imports.** Output is real
  OpenTimelineIO and a real CMX3600 EDL — verified in CI by reading them back
  through `opentimelineio` (OTIO round-trip) and its `cmx_3600` adapter. No
  bespoke JSON your conform stage can't open.
- **Provenance survives.** Each change carries its `source` (the notes file or a
  Frame.io comment id) and a `confidence`, stamped into the OTIO
  `metadata["cutlist"]` namespace and EDL comments — so an assistant editor can
  triage by certainty and trace every change back to who asked for it.
- **Frame.io in, timeline out.** Point a `--frameio` export at it and the
  comment thread folds into the same change list as the prose notes, anchored by
  Frame.io's own timecodes.
- **Runs with no API key, no network.** The default backend is a deterministic
  offline stand-in, so the tool (and its whole test suite) is hermetic. A real
  provider (Anthropic / OpenAI) is one flag away when you want semantic
  interpretation of the prose.
- **Token/cost telemetry on every run.** Because it's built on `replykit`, every
  extraction reports input/output tokens, repair attempts, and estimated USD —
  in the table and in `--json` — so spend is visible per job, not per month.

## Status

v0.1.0. Public API and CLI surface are stable; see [CHANGELOG.md](CHANGELOG.md).

## Install

```bash
pip install cutlist
```

This pulls in `replykit`, `opentimelineio` + the `cmx_3600` adapter
(`opentimelineio-plugins`), and the Typer/Rich CLI. Python 3.11+.

## Quickstart

```bash
# Extract and inspect (offline, no API key needed):
cutlist parse examples/notes.txt --fps 24

# Write both studio deliverables:
cutlist export examples/notes.txt --format both --fps 24 -o review
#   -> review.otio  (OpenTimelineIO)
#   -> review.edl   (CMX3600 EDL)

# Fold a Frame.io comment export into the same run:
cutlist parse examples/notes.txt --frameio examples/frameio_comments.csv

# Machine mode — exactly one JSON object on stdout, for pipelines:
cutlist parse examples/notes.txt --json | jq '.requests[] | {action, at, start, end}'
```

`cutlist parse examples/notes.txt` renders a table like:

```
notes — 6 change(s) @ 24 fps
┏━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ # ┃ action ┃ anchor                  ┃ conf ┃ rationale                   ┃ source ┃
┡━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ 1 │ flag   │ 01:00:05:00             │ 0.50 │ punch in on the speaker…    │ notes  │
│ 2 │ flag   │ 01:00:12:00–01:00:19:12 │ 0.50 │ the pause drags; lose ~4s   │ notes  │
│ … │        │                         │      │                             │        │
└───┴────────┴─────────────────────────┴──────┴─────────────────────────────┴────────┘
```

…followed by a token/cost **Telemetry** panel.

## CLI

Two commands. Both accept `--json`, `--fps`, `--frameio`, and `--model`.

### `cutlist parse NOTES.txt`

Extract change requests and display them.

| Option | Default | Meaning |
| --- | --- | --- |
| `--fps FLOAT` | `24.0` | Frame rate for timecode parsing (non-drop). |
| `--frameio CSV` | — | Frame.io comment export to fold in. |
| `--model NAME` | `heuristic` | Extraction backend: `heuristic` (offline) \| `anthropic` \| `openai`. |
| `--name ID` | `claude-opus-4-8` | Provider model id (for `anthropic`/`openai` + cost estimate). |
| `--max-steps N` | `16` | Hard cap on agent steps. |
| `--json` | off | Emit exactly one JSON object to stdout (machine mode). |

### `cutlist export NOTES.txt`

Extract, then write the deliverable(s).

| Option | Default | Meaning |
| --- | --- | --- |
| `--format`, `-f` | `otio` | `otio` \| `edl` \| `both`. |
| `--output`, `-o` | `cutlist` | Output path stem; the extension is added per format. |
| `--fps`, `--frameio`, `--model`, `--name`, `--max-steps`, `--json` | | as above |

### The `--json` contract

With `--json`, each command prints **exactly one JSON object** to stdout and
nothing else (diagnostics go to stderr; errors become a `{"error": "..."}`
object with a non-zero exit). `parse` emits the full `ChangeList` plus telemetry:

```json
{
  "title": "notes",
  "fps": 24.0,
  "requests": [
    {"action": "flag", "rationale": "punch in on the speaker…",
     "confidence": 0.5, "source": "notes", "at": "01:00:05:00"},
    {"action": "flag", "rationale": "the pause drags; lose ~4s",
     "confidence": 0.5, "source": "notes",
     "start": "01:00:12:00", "end": "01:00:19:12"}
  ],
  "telemetry": {"calls": 2, "total_input_tokens": 992, "total_output_tokens": 291,
                "total_repair_attempts": 0, "total_cost_usd": 0.0, "by_call": []},
  "errors": []
}
```

A point note carries a flat `at`; a range note carries flat `start`/`end`. The
object reloads losslessly via `ChangeList.from_dict(...)`. `export` emits the
written paths plus the same telemetry envelope.

### Using a real model

The default `heuristic` backend is a deterministic, offline stand-in: it anchors
a change on every timecode it finds in the notes, so the tool is useful and fully
hermetic with no credentials. For semantic interpretation of the prose (mapping
"tighten", "lose four seconds", "warm it up" onto the action vocabulary), opt in:

```bash
export ANTHROPIC_API_KEY=sk-...
cutlist parse examples/notes.txt --model anthropic --name claude-opus-4-8
```

The adapter and its SDK are imported lazily; a missing SDK or key fails with a
clear message, never a traceback.

## How it works

```
notes.txt ─┐
           ├─► ingest ─► replykit Agent (emit_change tool) ─► ChangeList ─┬─► OTIO (.otio)
frameio.csv┘            deterministic timecode parsing                    └─► EDL  (.edl)
```

1. **Ingest** (`cutlist.ingest`) — split the notes `.txt` on blank lines into
   fragments; tolerantly read a Frame.io CSV (fuzzy header matching) into the
   same fragment list, carrying each comment's out-of-band timecode as a hint.
2. **Extract** (`cutlist.extract`) — a `replykit.Agent` drives a single
   `emit_change` tool. The model reads the fragments and calls it once per
   discrete change; each call's args are parsed **deterministically** against the
   project fps into a `ChangeRequest`. A bad note is recorded, not fatal — one
   malformed line never loses the rest.
3. **Export** (`cutlist.otio_export` / `cutlist.edl_export`) — the sorted
   `ChangeList` becomes an OTIO timeline (markers/clips with `cutlist` metadata)
   and/or a CMX3600 EDL (numbered events, rationale comments, action/confidence
   annotations).

A runnable, end-to-end version of this lives in
[`examples/run_cutlist.py`](examples/run_cutlist.py) — see
[`examples/README.md`](examples/README.md).

## Interop & correctness

Correctness is the product, and it's enforced in CI:

- **OTIO output round-trips.** The written `.otio` reads back through
  `opentimelineio`, and re-serializing it is byte-stable
  (`write(read(write(tl))) == write(read(...))`).
- **EDL output parses in NLEs.** The written `.edl` parses cleanly through
  OTIO's `cmx_3600` adapter — the same parser editorial tools use — and matches
  byte-level goldens.
- **Timecode is deterministic.** Non-drop-frame only in v0.1; a drop-frame `;`
  separator is rejected with a clear error rather than silently mis-parsed.

The whole test suite is **hermetic** — no network, no live LLM — driving
`replykit`'s `ScriptedModel` and the offline `HeuristicModel`.

## Development

```bash
uv venv && uv pip install -e '/path/to/replykit' && uv pip install -e '.[dev]'
uv run ruff check . && uv run pytest --cov=cutlist --cov-report=term-missing
```

The whole suite is hermetic — no network, no live LLM. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Edgar Hernandez.
