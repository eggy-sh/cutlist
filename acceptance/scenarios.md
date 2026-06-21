# cutlist — Scenario Acceptance Suite

End-to-end acceptance scenarios for `cutlist`, the editor's-notes router that turns
free-form review prose (plus optional Frame.io comment exports) into timecode-anchored
change requests and exports them to OpenTimelineIO (`.otio`) and CMX3600 EDL (`.edl`).

## Scope & ground rules

- **Hermetic.** Every scenario runs with **no network and no live LLM**. The extraction
  backend is either the built-in offline `HeuristicModel` (the CLI default) or a
  `replykit.ScriptedModel` injected by monkeypatching `cutlist.cli._build_model`. No
  scenario calls `--model anthropic` / `--model openai` against a real provider.
- **AI discipline.** The model is the *only* place judgment lives, and these scenarios
  never add a model call. All timecode math, format/round-trip, CSV parsing, action
  normalization, DB-free formatting, exit codes, and the `--json` contract are
  **deterministic** and are what the success criteria check. The "semantic" scenarios use
  a `ScriptedModel` to stand in for a real LLM's emissions, so the *plumbing* (provenance,
  action-vocabulary coercion, malformed-note tolerance) is exercised without nondeterminism.
- **Evaluation method (single, consistent): pass/fail.** Every scenario below has an
  explicit, machine-checkable success criterion (exact exit code, specific `--json` field
  values, artifact validity via OTIO/EDL round-trip, or a structural invariant). There are
  **no rubric/graded scenarios** — there is no free-text judgment output to grade; the
  product's contract is deterministic, so binary pass/fail is the correct method.
- **Interpreter / paths.** Use the repo venv. Commands below are written relative to the
  repo root `/Users/ehernand/personal_projects/postpro-kit/cutlist`; substitute absolute
  paths in CI. The CLI is `.venv/bin/cutlist`; the interpreter is `.venv/bin/python3`.
- **Fixtures.** `examples/notes.txt` (6 timecode anchors: 4 points, 2 ranges) and
  `examples/frameio_comments.csv` (4 comments at `01:00:05:00`, `01:00:22:00`,
  `01:00:58:00`, `01:01:25:00`; the first duplicates a notes anchor). Scratch inputs are
  written under a per-run temp dir.

## Capabilities under test

| ID | Capability |
| --- | --- |
| C1 | `parse` → human table + telemetry (offline heuristic backend) |
| C2 | `parse --json` machine contract (one JSON object, `ChangeList` + telemetry, lossless reload) |
| C3 | `export` to OTIO — artifact round-trips and carries `cutlist` metadata |
| C4 | `export` to EDL — artifact parses via OTIO `cmx_3600`, byte-deterministic |
| C5 | `export --format both` — writes both deliverables in one run |
| C6 | Frame.io CSV fold-in — tolerant header matching, extra anchors merged |
| C7 | Frame-rate-aware deterministic timecode (`--fps` changes the frame base; reload-faithful) |
| C8 | Semantic extraction plumbing via `ScriptedModel` — action-vocabulary coercion + provenance pass-through |
| C9 | Robustness — malformed notes are skipped (recorded in `errors[]`), never fatal |
| C10 | Error contract — missing files / bad flags / empty result → exit 1, clean `{"error": ...}`, stdout/stderr isolation |

---

## Scenarios

Each scenario is `pass/fail`. "Check" lines are the exact machine-checkable assertions.

### S1 — `parse` human mode renders table + telemetry  *(C1)* — passfail
**Setup:** none beyond fixtures.
**Run:** `.venv/bin/cutlist parse examples/notes.txt`
**Success criteria (ALL must hold):**
- Exit code `== 0`.
- stdout contains the table header tokens `action`, `anchor`, `rationale`, `source`.
- stdout contains `Telemetry` and `estimated cost (USD)` with a `$0.000000` value
  (offline model is free).
- stdout contains the literal anchors `01:00:05:00` and `01:00:30:00` (point notes) and
  the range rendering `01:00:12:00–01:00:19:12` (en-dash separator).
- The title line reads `notes — 6 change(s) @ 24 fps`.

### S2 — `parse --json` is exactly one object and reloads losslessly  *(C2)* — passfail
**Run:** `.venv/bin/cutlist parse examples/notes.txt --json`
**Success criteria:**
- Exit code `== 0`.
- stdout is **exactly one** non-blank line and parses as a single JSON object (nothing
  else on stdout).
- `obj["title"] == "notes"`, `obj["fps"] == 24.0`, `len(obj["requests"]) == 6`.
- Each request has either a flat `"at"` (point) **or** flat `"start"`+`"end"` (range),
  never both; every `action == "flag"` under the heuristic; every `confidence == 0.5`;
  every `source == "notes"`.
- `obj["telemetry"]["calls"] >= 1` and `obj["telemetry"]["total_cost_usd"] == 0.0`.
- `obj["errors"] == []`.
- Reload check: `cutlist.models.ChangeList.from_dict(obj)` yields a list with
  `len == 6` and `fps == 24.0` (lossless round-trip of the payload).
- Requests are sorted ascending by start anchor: the list of
  `req.get("at") or req.get("start")` equals its own `sorted()`.

### S3 — `export` OTIO round-trips and carries cutlist metadata  *(C3)* — passfail
**Run:** `.venv/bin/cutlist export examples/notes.txt --format otio -o $TMP/review --json`
**Success criteria:**
- Exit code `== 0`; `obj["format"] == "otio"`; `obj["changes"] == 6`;
  `obj["written"]` is a 1-element list whose single path ends in `.otio` and exists.
- OTIO validity (read back via `opentimelineio`):
  - File reads via `otio.adapters.read_from_file(path)` without error; `len(tl.tracks) == 1`.
  - Re-serialize round-trip is byte-stable:
    `write(read(write(tl))) == write(read(...))` (i.e. `s1 == s2` for
    `s1 = write_to_string(tl)`, `s2 = write_to_string(read_from_string(s1))`).
  - The track holds **4 Markers** (point notes) and **2 Clips** (range notes), totalling 6.
  - Every Marker/Clip carries `metadata["cutlist"]` with exactly the keys
    `{action, confidence, rationale, source}`.

### S4 — `export` EDL parses via cmx_3600 and is byte-deterministic  *(C4)* — passfail
**Run:** `.venv/bin/cutlist export examples/notes.txt --format edl -o $TMP/review --json`
(then a second run to `$TMP/review2`)
**Success criteria:**
- Exit code `== 0`; `obj["format"] == "edl"`; single written path ends in `.edl` and exists.
- The file body starts with `TITLE: notes` and contains a line `FCM: NON-DROP FRAME`.
- It parses cleanly via `otio.adapters.read_from_file(path, "cmx_3600")` (non-None
  timeline), and that timeline contains **6** events (clips).
- Determinism: a second export of the same input to a different path is **byte-identical**
  to the first (`diff` exits 0).

### S5 — `export --format both` writes both deliverables  *(C5)* — passfail
**Run:** `.venv/bin/cutlist export examples/notes.txt --format both -o $TMP/review --json`
**Success criteria:**
- Exit code `== 0`; `obj["format"] == "both"`; `obj["changes"] == 6`.
- `sorted(Path(p).suffix for p in obj["written"]) == [".edl", ".otio"]`; both files exist.
- The `.otio` reads back via `otio.adapters.read_from_file(...)` and the `.edl` reads back
  via the `cmx_3600` adapter — both without error.

### S6 — Frame.io CSV folds in and adds anchors (tolerant headers)  *(C6)* — passfail
**Run (baseline):** `.venv/bin/cutlist parse examples/notes.txt --json` → `base`.
**Run (merged):** `.venv/bin/cutlist parse examples/notes.txt --frameio examples/frameio_comments.csv --json` → `merged`.
**Success criteria:**
- Both exit `0`.
- `len(merged["requests"]) > len(base["requests"])` (specifically `9 > 6`: the CSV
  contributes the three non-duplicate anchors).
- The merged anchor set (`req["at"] or req["start"]`) contains `01:00:22:00`,
  `01:00:58:00`, and `01:01:25:00` (the new Frame.io timecodes) and still contains all six
  original notes anchors.
- The merged result still parses as exactly one JSON object and reloads via
  `ChangeList.from_dict` without error.

> Note: under the offline `HeuristicModel`, every emitted `source` is `"notes"` (the
> heuristic does not propagate per-comment provenance). Provenance pass-through from a
> model's emissions is covered separately in **S8**.

### S7 — `--fps` changes the deterministic frame base, faithfully mirrored  *(C7)* — passfail
**Run:** `.venv/bin/cutlist parse examples/notes.txt --fps 30 --json`
**Plus a unit-level timecode check** (deterministic math, no model):
**Success criteria:**
- Parse exits `0`; `obj["fps"] == 30.0`; the JSON reloads via `ChangeList.from_dict`
  (so the chosen fps is carried and the timecodes re-parse at 30fps without error).
- Timecode determinism (via `cutlist.timecode.Timecode`):
  `Timecode.from_string("01:00:05:00", 24).frame == 86520` and
  `Timecode.from_string("01:00:05:00", 30).frame == 108150` (same string, different base).
- Drop-frame guard: `Timecode.from_string("01:00:05;00", 24)` raises `TimecodeError`
  whose message mentions `drop-frame` (a `;` is rejected, not silently mis-parsed).

### S8 — Scripted "real model" emissions flow through: action coercion + provenance  *(C8)* — passfail
**Setup:** monkeypatch `cutlist.cli._build_model` to return a
`replykit.ScriptedModel` whose turns emit two `emit_change` blocks then a final answer:
1. `action = tighten` (synonym), `start=01:00:12:00`, `end=01:00:19:12`, `source = c-202`;
2. `action = warm` (synonym), `at = 01:00:45:00`, `source = notes`;
then a plain-text turn to end the agent loop.
**Run:** `.venv/bin/cutlist parse <notes> --model anthropic --json` (backend name is
irrelevant once `_build_model` is patched).
**Success criteria:**
- Exit `0`; `len(obj["requests"]) == 2`; `obj["errors"] == []`.
- Action vocabulary coercion: the request actions are `["trim", "color"]` —
  `tighten → trim` and `warm → color` per the synonym table (model verbs normalized onto
  the closed `Action` enum, deterministically).
- Provenance pass-through: one request has `source == "c-202"` (the Frame.io comment id the
  model emitted), proving non-`"notes"` provenance survives end-to-end.
- The range request carries flat `start`/`end`; the point request carries flat `at`; the
  payload reloads via `ChangeList.from_dict`.

### S9 — Malformed note is skipped and surfaced, never fatal  *(C9)* — passfail
**Setup:** monkeypatch `_build_model` to a `ScriptedModel` emitting two `emit_change`
blocks then a final answer: one **valid** (`at = 01:00:05:00`) and one with an
**out-of-range timecode** (`at = 01:00:99:00`), same final-answer turn.
**Run:** `.venv/bin/cutlist parse <notes> --model anthropic --json`
**Success criteria:**
- Exit code `== 0` (a bad note must **not** crash the run).
- `len(obj["requests"]) == 1` (only the valid change survives).
- `len(obj["errors"]) == 1` and that error string mentions the offending field
  (e.g. contains `seconds field out of range`), so the dropped note is visible, not silent.
- stdout is still exactly one JSON object.

### S10 — Error contract: missing file / unknown model / bad format / empty result  *(C10)* — passfail
Four deterministic failure modes, each its own assertion; all share the JSON error shape
(exactly one `{"error": "..."}` object on stdout, exit `1`):
- **Missing notes file:** `.venv/bin/cutlist parse $TMP/nope.txt --json` → exit `1`,
  `obj["error"]` contains `not found`.
- **Unknown backend:** `.venv/bin/cutlist parse examples/notes.txt --model telepathy --json`
  → exit `1`, `obj["error"]` contains `unknown --model`.
- **Bad export format:** `.venv/bin/cutlist export examples/notes.txt --format xml -o $TMP/x --json`
  → exit `1`, `obj["error"]` contains `unknown --format`.
- **Empty change list export:** an input with no timecodes,
  `.venv/bin/cutlist export $TMP/empty.txt --format otio -o $TMP/e --json` → exit `1`,
  `obj["error"]` contains `empty ChangeList`.
- **stdout/stderr isolation (human mode):** `.venv/bin/cutlist parse $TMP/nope.txt` (no
  `--json`) → exit `1`, **stdout is empty**, and stderr contains `not found`.

### S11 — Frame.io CSV edge cases: fuzzy headers + no-text-column failure  *(C6 edge)* — passfail
**Setup:** write `$TMP/weird.csv` with headers `Comment ID,TC,Body,Author` and two rows
(one with a timecode, one without), and `$TMP/notext.csv` with headers `Foo,Bar`.
**Checks:**
- Fuzzy match: `cutlist.ingest.load_frameio_csv("$TMP/weird.csv")` returns 2 notes; the
  `Body` column is selected as the comment text, `Comment ID` becomes each note's `source`
  (e.g. `x-1`), and the `TC` column populates `timecode_hint` where present (and `None`
  where the cell is blank).
- No-text-column failure surfaces cleanly through the CLI:
  `.venv/bin/cutlist parse examples/notes.txt --frameio $TMP/notext.csv --json` → exit `1`,
  `obj["error"]` contains `no comment-text column found`.

---

## Coverage matrix

| Capability | Scenario(s) |
| --- | --- |
| C1 parse human/table+telemetry | S1 |
| C2 parse --json contract + reload | S2 |
| C3 OTIO export round-trip + metadata | S3 |
| C4 EDL export cmx_3600 + determinism | S4 |
| C5 export both | S5 |
| C6 Frame.io fold-in (+ tolerant/edge) | S6, S11 |
| C7 fps-aware deterministic timecode | S7 |
| C8 model-emission plumbing (coercion/provenance) | S8 |
| C9 malformed-note tolerance | S9 |
| C10 error contract + stream isolation | S10 |

Edge/failure scenarios: **S9** (malformed note), **S10** (four error modes + stream
isolation), **S11** (CSV edge cases). 11 scenarios total, all `pass/fail`.

## How to run

The reference assertions are already encoded in the hermetic test suite
(`tests/test_cli.py`, `tests/test_timecode.py`, `tests/test_ingest.py`,
`tests/test_extract.py`, `tests/test_otio_export.py`, `tests/test_edl_export.py`,
`tests/test_models.py`). Run:

```bash
.venv/bin/python3 -m pytest -q
```

A scenario passes iff every "Success criteria" bullet for it holds. The suite is hermetic
(no network, no live LLM); the `--model anthropic` paths in S8/S9 are driven by a
`ScriptedModel` via monkeypatch, never a real provider.
