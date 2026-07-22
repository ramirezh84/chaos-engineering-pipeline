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
    app-a-infra-components-by-env.EXAMPLE.md       <- example of the per-service infra declaration format
  examples/
    inputs/
      01-account-origination-COMPLETE.json       <- real journey graph (nodes/edges/grades)
      app-a.json           <- real per-service scan card
      infra-md/app-a-infra-components-by-env.md    <- real per-service infra declaration
    outputs/
      Chaos_Package_EXAMPLE.xlsx                 <- what the generator produced from those inputs
      Chaos_Dashboard_EXAMPLE.html               <- same data as an interactive single-file dashboard
```

## Prerequisites

- Python 3 with `openpyxl` (`pip install --user openpyxl`). Nothing else. No network needed at run time.
- For the repo-scan phases (producing new scan cards / infra-mds): Claude Code (Opus 4.8, 1M context)
  on the VDI, following `spec.md`. Optional accelerators (`mvn`, `terraform`, `rg`, `node`) are
  detected by the spec's preflight — absent tools degrade gracefully to static parsing, never to guessing.

## Verify the package works (2 minutes, uses the bundled examples)

```bash
cd chaos-pipeline
python3 chaos_package_generator.py \
  --root examples/inputs \
  --scan-cards examples/inputs \
  --infra-md examples/inputs/infra-md \
  --components components.template.json \
  --out /tmp/package.xlsx --html /tmp/dashboard.html
```

Expected: `scenarios: 118  assertions: 145  coverage gaps: 23`, and `/tmp/dashboard.html`
opens in any browser. If you see those numbers, the pipeline is intact.

## The real operating cycle

1. **Produce/refresh evidence** (Claude Code, per `spec.md`):
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
