# Chaos Engineering Pipeline — Package Contents & Quick Start

Repeatable pipeline: **evidence files in → scored chaos engineering package out.**
No scenario is generated without evidence; everything re-runs as services and journeys grow.

## Contents

```
chaos-pipeline/
  README.md                          <- this file
  spec.md                            <- the Claude Code runbook (Phases 0-7). START HERE for the repo scans.
  chaos_package_generator.py         <- the package generator (inputs -> xlsx + html dashboard)
  consolidate.py                     <- merges Phase 2-4 scan output into the methodology workbook
  journey_ingest.py                  <- lightweight utility: rank journey edges, find coverage gaps (JSON out)
  components.template.json           <- per-service infra fallback declaration (fill in; retired by infra-md files)
  workbook/
    Chaos_Engineering_Scenario_Catalog_v2.xlsx   <- methodology workbook: 90-scenario hand catalog,
                                                    Service Inventory, App & Infra Scan Instructions tabs,
                                                    Priority Model, Wave Plan. consolidate.py appends to a COPY.
  templates/
    als-infra-components-by-env.EXAMPLE.md       <- example of the per-service infra declaration format
  examples/
    inputs/
      01-account-origination-COMPLETE.json       <- real journey graph (nodes/edges/grades)
      account-locator-service.json           <- real per-service scan card
      infra-md/als-infra-components-by-env.md    <- real per-service infra declaration
    outputs/
      Chaos_Package_EXAMPLE.xlsx                 <- what the generator produced from those inputs
      Chaos_Dashboard_EXAMPLE.html               <- same data as an interactive single-file dashboard
```

## Input folder layout

Discovery is **content-based for all three artifact types** — journeys, scan cards, and infra
documents are recognized by what's inside them, not by filename or folder. Any structure works;
organize for humans.

A layout that scales to 17+ services and 16+ journeys:

```
src/
  journeys/
    UJ01-AccountOrigination.json
    UJ02-....json
  services/
    S01-DLC/
      S01-DLC-ScoreCard.json
      S01-DLC-Infrastructure-Components.md
    S02-ARM/
      S02-ARM-ScoreCard.json
      S02-ARM-Infrastructure-Components.md
```

Then point all three flags at the same root — the tool sorts the files out itself:

```bash
python3 chaos_package_generator.py --root src --scan-cards src --infra-md src --check
```

**Naming conventions that help (humans, not the parser):**

- Numeric prefixes (`UJ01`, `S01`) sort predictably and let people correlate artifacts at a
  glance. Aligning the service number with the repo number (`01-dlc-...` -> `S01-DLC`) makes
  the mapping obvious.
- Keep one identifier scheme per artifact family and don't encode dates or versions in
  filenames — provenance already lives inside the artifacts (git SHAs, scan dates).
- A platform name in every filename is harmless but redundant if everything belongs to one
  platform; drop it unless you expect a second platform later.

**The one thing that actually matters:** the join key is the **canonical service id inside the
files**, never the filename.

- Each scan card needs a top-level `"service": "<canonical-id>"`.
- Each infra document should carry a `service: <canonical-id>` line near the top.
- That id must match the journey graph's node `id` exactly.

Filenames can be anything; if those ids don't line up, the artifacts won't join.

## Check your inputs before generating

```bash
python3 chaos_package_generator.py --root src --scan-cards src --infra-md src --check
```

Generates nothing. Reports: every file found by type, how each journey node joins to a scan
card / infra document / declared components, any JSON that was ignored and exactly why, nodes
that will fall through to coverage gaps, and any infra document whose service id had to be
guessed from its filename. Run this first on any new artifact set — it turns silent join
failures into an explicit list.

## Prerequisites

- Python 3 with `openpyxl` (`pip install --user openpyxl`). Nothing else. No network needed at run time.
- For the repo-scan phases (producing new scan cards / infra-mds): Claude Code (Opus 4.8, 1M context)
  on the VDI, following `spec.md`. Optional accelerators (`mvn`, `terraform`, `rg`, `node`) are
  detected by the spec's preflight — absent tools degrade gracefully to static parsing, never to guessing.

## Verify the package works (2 minutes, uses the bundled examples)

**macOS / Linux / Git Bash:**

```bash
cd chaos-pipeline
python3 chaos_package_generator.py \
  --root examples/inputs \
  --scan-cards examples/inputs \
  --infra-md examples/inputs/infra-md \
  --components components.template.json \
  --out out/package.xlsx --html out/dashboard.html
```

**Windows (PowerShell or CMD) — single line, and `python` instead of `python3`:**

```
python chaos_package_generator.py --root examples/inputs --scan-cards examples/inputs --infra-md examples/inputs/infra-md --components components.template.json --out out\package.xlsx --html out\dashboard.html
```

Expected: `scenarios: 118  assertions: 145  coverage gaps: 23`, and `out/dashboard.html`
opens in any browser. If you see those numbers, the pipeline is intact. The `out/` folder is
created automatically if it doesn't exist.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `No such file or directory: '/tmp/package.xlsx'` | `/tmp` doesn't exist on Windows. Use a relative path like `out\package.xlsx` (the command above). Recent versions also create the output folder automatically. |
| `python3: command not found` (Windows) | Use `python` or `py -3` instead. |
| The backslash line-continuations error out | PowerShell and CMD don't use `\` for line continuation — run the single-line Windows command above. |
| `ModuleNotFoundError: openpyxl` | `pip install --user openpyxl` (or `python -m pip install --user openpyxl`). |
| `No journey files found` | `--root` must point at a folder containing journey JSONs (top-level `journey`/`nodes`/`edges` keys). Discovery is by content, so any filename works — but the path must be right. |

## The real operating cycle

> **If another process already produces your journey graphs, service scan cards, and
> `<app>-infra-components-by-env.md` files, skip step 1 entirely** — the generator reads those
> artifacts directly and never touches repositories. Run step 2, read the Coverage Gaps sheet,
> and scan only for what the gaps actually name. See "READ FIRST" at the top of `spec.md`.

1. **Produce/refresh evidence** (Claude Code, per `spec.md`) — *only if the artifacts don't
   already exist*:
   - Phases 0-2: pull the app repos, emit one scan-card JSON per service.
   - Phase 3: scan the infra repos; produce/update each `<app>-infra-components-by-env.md`.
   - Phases 4-6 (optional but recommended): profile diffs, workbook consolidation
     (`consolidate.py`), and `standards.md` synthesis.
   - Journey files: produced by your existing journey-mapping sessions; drop them anywhere
     under the root — discovery is content-based (top-level `journey`/`nodes`/`edges`),
     names and folders don't matter.
2. **Generate the package**: run `chaos_package_generator.py` (command above, pointed at your
   real dirs). Outputs: the xlsx package (chaos team's writable working copy) and the html
   dashboard (read-only review/leadership surface).
3. **Execute experiments**, record results in the xlsx Status column, feed confirmed findings
   into `standards.md`.
4. **Repeat** on any change: new service, new journey, config change, or quarterly drift check.

## Trust model (read the Status column this way)

- `CONFIRMED_DEFECT` — code already proves the bug; file it, fix it; the scenario is the regression guard.
- `Proposed (evidence-confirmed …)` — generated from cited code/infra evidence.
- `DECLARED — confirm with infra scan` — from components.json; upgrade by producing the infra-md.
- `NEEDS_ASSESSMENT / NEEDS COMPLIANCE REVIEW` — human decision required before scheduling.
- Coverage Gaps tab — what was deliberately NOT generated, and why. An empty gaps tab on a
  partial input set means something is wrong, not that coverage is complete.

## Known limits (honest list)

- The infra-md parser is keyword/regex based against the current document format; facts it
  can't recognize are simply not claimed. Harden it with each new service's file.
- Scoring weights (criticality/data-class bumps, base L/S per template) are proposals in one
  place at the top of the generator — recalibrate against incident history.
- The scan-card schema in spec.md Section 4.2 should be reconciled to your working scanner's
  real card format (spec Section 9.3 covers this).
- Batch-journey `via` values (`s3-event`, `eventbridge-schedule`) are pre-wired but untested —
  no batch journey file exists yet.
- The full 34-repo scan has not been run; everything here is proven at 2-input scale.
