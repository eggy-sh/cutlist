# cutlist examples

Runnable, fully hermetic examples — no API key, no network. They use the CLI's
offline `HeuristicModel`, so the output is deterministic.

## Files

| File | What it is |
| --- | --- |
| [`notes.txt`](notes.txt) | A free-form director/editor review-notes file: point notes, range notes, a misspelled-title flag, a color note, an insert, and a fade. |
| [`frameio_comments.csv`](frameio_comments.csv) | A Frame.io comment export. The column names (`Comment ID`, `Timecode`, `Comment Text`, …) intentionally differ from the obvious ones to exercise the tolerant header matcher. |
| [`run_cutlist.py`](run_cutlist.py) | An end-to-end Python walkthrough: ingest → extract → export both formats, with telemetry. |

## Run the script

```bash
python examples/run_cutlist.py
```

It ingests `notes.txt` + `frameio_comments.csv`, extracts the change list, prints
each change and the replykit token/cost telemetry, and writes
`examples/build/review.otio` and `examples/build/review.edl` (a gitignored
scratch dir).

To do real, semantic extraction instead of the offline heuristic, swap the model
in the script for `replykit.AnthropicModel()` (with `ANTHROPIC_API_KEY` set).

## Or drive the CLI directly

```bash
# Human-readable table + telemetry:
cutlist parse examples/notes.txt --fps 24

# Fold in the Frame.io comments:
cutlist parse examples/notes.txt --frameio examples/frameio_comments.csv

# Write both deliverables:
cutlist export examples/notes.txt --format both --fps 24 -o review

# Machine mode for pipelines — exactly one JSON object on stdout:
cutlist parse examples/notes.txt --json | jq '.requests[] | {action, at, start, end}'
```

## Verify the output is real interchange

```bash
cutlist export examples/notes.txt --format both -o /tmp/review

# OTIO reads back through opentimelineio:
python -c "import opentimelineio as otio; print(otio.adapters.read_from_file('/tmp/review.otio').name)"

# EDL parses through the cmx_3600 adapter NLEs use:
python -c "import opentimelineio as otio; print(otio.adapters.read_from_file('/tmp/review.edl', 'cmx_3600').name)"
```
