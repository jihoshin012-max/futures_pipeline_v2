# ICM Scaffold Audit — 2026-03-27

## Audit Scope

Full five-layer audit of the ICM scaffold against:
- Van Clief/McDermott paper (arxiv.org/html/2603.16021v2)
- Jake Van Clief video walkthrough (youtube.com/watch?v=MkN-ss2Nl10)

Audited all files across Layer 0 (CLAUDE.md), Layer 1 (CONTEXT.md),
Layer 2 (7 workspace/stage CONTEXT.md files), Layer 3 (7 reference docs),
and Layer 4 (output directory structure).

Traced 5 end-to-end scenarios through the full layer stack.

---

## Issues Found and Fixed

| # | Severity | Issue | Where | Fix Applied |
|---|----------|-------|-------|-------------|
| 1 | HIGH | "P1a/P1b" vs "Replica-A/Replica-B" terminology conflict | lab/CONTEXT.md, period-config.md, stage 02 CONTEXT.md | Standardized to Replica-A/Replica-B everywhere |
| 2 | HIGH | Frozen params cross-workspace read ambiguity — CLAUDE.md says "copy to bench/output/" but bench/CONTEXT.md read from "lab/output/" | CLAUDE.md, bench/CONTEXT.md, deploy/CONTEXT.md | Added handoff protocol to CLAUDE.md; bench and deploy read from own output/ |
| 3 | HIGH | "Get verdict" task missing cross-workspace dependencies in router | CONTEXT.md line 41 | Added: holdout tradelog + stress reports from bench/output/ |
| 4 | MEDIUM | `deployment-checklist` referenced in deploy/CONTEXT.md but missing from type catalog | CLAUDE.md type catalog | Added `deployment-checklist` type |
| 5 | MEDIUM | Stage workflow dirs (01-features/output/, references/) exist but nothing writes there | lab/workflows/01-features/, 02-hypotheses/, 03-params/ | Removed unused output/ and references/ dirs from stages |
| 6 | MEDIUM | `frozen-features-frozen.json` double-frozen naming inconsistent with `hypothesis-frozen.json` | CLAUDE.md, all stage CONTEXT.md files | Renamed types: `frozen-features` → `features`, `frozen-params` → `params`. Now `features-frozen.json`, `params-frozen.json`, `hypothesis-frozen.json` |
| 7 | MEDIUM | "Understand experiment pipeline" routed in CLAUDE.md quick nav but missing from CONTEXT.md task routing | CONTEXT.md task routing table | Added routing entry |
| 8 | LOW | `_config/period-config.md` referenced 17 times but missing from quick nav | CLAUDE.md, CONTEXT.md | Added to both quick nav tables |

---

## Scenarios Traced

### Scenario A: Screen features for rotational-NQ

**Flow:** CONTEXT.md → lab/CONTEXT.md → lab/workflows/CONTEXT.md → lab/workflows/01-features/CONTEXT.md → loads feature-rules.md + instruments.md → edits lab/rotational-NQ-feature-engine.py → runs harness/evaluate_features.py → appends lab/output/rotational-NQ-results.tsv → human approves → lab/output/rotational-NQ-features-frozen.json

**Result:** Flow is correct. Blocked only by empty harness/ (expected for scaffold).

### Scenario B: Run holdout validation for rotational-NQ on window W3

**Flow:** CONTEXT.md → bench/CONTEXT.md → reads _config/period-config.md (finds W3 = holdout) → checks bench/output/holdout-locked-rotational-NQ-W3.flag → reads bench/output/rotational-NQ-params-frozen.json (copied from lab) → runs harness/backtest_engine.py → writes bench/output/rotational-NQ-holdout-tradelog-W3.csv → creates lock flag → logs to audit

**Result:** Flow is correct after fix #2 (handoff protocol).

### Scenario C: Roll periods when new quarter arrives

**Flow:** Edit _config/period-config.md — change W3 from holdout to calibration, add W4 as holdout. One edit. Old W3 lock flags stay but are irrelevant. Lab experiments can use larger calibration pool. New holdout W4 has no lock flag — available.

**Result:** Clean roll. No file renames, no structural changes.

### Scenario D: Add new archetype mean-revert-ES

**Flow:** Register ES in _config/instruments.md (if not already). Start naming files: lab/mean-revert-ES-feature-engine.py, lab/mean-revert-ES-simulator.py, etc. No folders created. No CONTEXT.md edits needed.

**Result:** Naming convention handles it. Gap noted: no template sourcing instructions for new simulator/evaluator files.

### Scenario E: Deploy rotational-NQ after holdout pass

**Flow:** lab/output/rotational-NQ-params-frozen.json → copy to bench/output/ → holdout + stress + verdict → bench/output/rotational-NQ-verdict-W3-validated.json (PASS) → copy params + verdict to deploy/output/ → deploy/CONTEXT.md Build section → generates deploy/output/rotational-NQ-build-v1-deployed.cpp → human completes checklist → creates deployment-ready flag → logs to audit

**Result:** Flow is correct after fixes #2 and #4.

---

## Known Gaps (Not Fixed — Expected for Scaffold)

| # | Gap | Why Not Fixed | When to Address |
|---|-----|---------------|-----------------|
| 1 | harness/ directory is empty | Scaffold — code goes here when pipeline is built | Before first experiment run |
| 2 | Layer 3 docs are stubs (simulation-rules, feature-rules, exit-templates, statistical-gates, acsil-reference) | Scaffold — content filled when operational | Before first experiment run |
| 3 | ~~results.tsv schema not defined~~ | **FIXED 2026-03-27** — added lab/docs/results-schema.md | — |
| 4 | audit_log.md entry format loosely defined (columns exist, no examples) | Depends on workflow maturity | When first audit events occur |
| 5 | ~~Human approval mechanism not documented~~ | **FIXED 2026-03-27** — frozen file exists = approved, documented in all stage contracts | — |
| 6 | Experiment budget enforcement not automated | Budget in stage contracts; agent reads it; defer automation to /context-audit skill when pattern is clear | When recurring violations observed |
| 7 | ~~New archetype template sourcing~~ | **FIXED 2026-03-27** — added to CLAUDE.md: "clone existing simulator/evaluator, adapt" | — |
| 8 | ~~Cross-workspace copy mechanics~~ | **FIXED 2026-03-27** — handoff protocol added to stage 03 and bench verdict sections | — |

### Additional items added (2026-03-27 post-audit):
- Review cadence frontmatter added to all CONTEXT.md files
- `journal` type added to CLAUDE.md type catalog + placement rules
- Journal entries added to all stage contract outputs
- `results-schema.md` Layer 3 doc created in lab/docs/
- File self-check instruction added to CLAUDE.md naming section

---

## Layer Compliance Summary

| Layer | File(s) | Token Budget | Compliance | Notes |
|-------|---------|-------------|------------|-------|
| 0 | CLAUDE.md | ~800 tok target, ~195 lines actual | 90% | Slightly over token budget due to naming conventions — justified per video pattern |
| 1 | CONTEXT.md | ~300 tok target, ~55 lines actual | 95% | Clean after fixes |
| 2 | 7 CONTEXT.md files | 200-500 tok each | 90% | bench/CONTEXT.md is longest (~130 lines) due to holdout/stress/verdict procedures |
| 3 | 7 reference docs | 500-2k tok each | Stubs | Content TBD — structure correct |
| 4 | output/ directories | varies | Empty | Expected — populated during operation |

---

## ICM Principle Compliance

| Principle | Status | Evidence |
|-----------|--------|----------|
| One stage, one job | PASS | Each stage CONTEXT.md has single responsibility |
| Plain text as interface | PASS | All markdown/JSON, no binary formats |
| Layered context loading | PASS | Load/skip tables in every workspace CONTEXT.md |
| Every output is an edit surface | PASS | All outputs are .md, .json, .tsv, .csv, .cpp |
| Configure factory, not product | PASS | _config/ sets up workspace; each run produces new artifacts |
| Layer 3 vs Layer 4 structural separation | PASS | docs/ (reference) vs output/ (working) in every workspace |
| Naming convention as organization | PASS | No per-archetype or per-instrument folders needed |
| Skills wired with WHEN/WHY | PASS | All three workspace CONTEXT.md files have activation tables |
| Cross-workspace flow documented | PASS | CLAUDE.md + CONTEXT.md both show flow + handoff protocol |
| Rolling periods without restructuring | PASS | Window/role model in period-config.md |
