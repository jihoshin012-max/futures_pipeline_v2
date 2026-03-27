# Futures Pipeline — Workspace Map

<!--
last_reviewed: 2026-03-27
review_cadence: quarterly

====================================================================
This is LAYER 0 — THE MAP.

CLAUDE.md is auto-loaded into every conversation. It's always in
context. That makes it prime real estate. Use it for:

1. Folder structure (so the agent always knows where things live)
2. ID systems & naming conventions (so files land in the right place)
3. File placement rules (so nothing gets lost)
4. Quick navigation table (task → workspace)

Do NOT put:
  - Experiment instructions (those go in workspace CONTEXT.md files)
  - Simulation rules (those go in lab/docs/)
  - Pipeline stage details (those go in lab/workflows/CONTEXT.md)
  - Statistical gates (those go in bench/docs/)

Keep it under 200 lines. Every line here costs tokens in EVERY
conversation.
====================================================================
-->

## What This Is

Ji's futures strategy research system. Three siloed workspaces —
lab, bench, deploy — each handling one part of the strategy
lifecycle. An agent drops into a workspace, reads its CONTEXT.md,
does its work, and exits.

**CONTEXT.md** (top-level) routes you to the right workspace.
This file is the map.

---

## Folder Structure

```
pipeline/
├── CLAUDE.md                              ← You are here (always loaded)
├── CONTEXT.md                             ← Task router
│
├── lab/                                   ← Run experiments, build strategies
│   ├── CONTEXT.md
│   ├── docs/                              ← Simulation rules, feature rules
│   ├── output/                            ← Results, findings, frozen configs
│   └── workflows/                         ← Experiment pipeline (3 stages)
│       ├── CONTEXT.md
│       ├── 01-features/
│       ├── 02-hypotheses/
│       └── 03-params/
│
├── bench/                                 ← Validate and judge strategies
│   ├── CONTEXT.md
│   ├── docs/                              ← Statistical gates
│   └── output/                            ← Holdout trade logs, stress tests, verdicts
│
├── deploy/                                ← Monitor live strategies
│   ├── CONTEXT.md
│   └── output/                            ← Verified builds, paper trades, drift
│
├── _config/                               ← Shared constants (read by all)
│   ├── instruments.md                        Tick size, tick value, sessions, costs
│   └── period-config.md                      Rolling windows + role assignments
│
├── harness/                               ← Fixed engines (never edit)
├── data/                                  ← Source bar/touch data
├── scoring/                               ← Scoring adapters + models
├── audit/                                 ← Append-only experiment log
└── tests/
```

---

## Quick Navigation

| Want to... | Go here |
|------------|---------|
| **Run experiments** | `lab/CONTEXT.md` |
| **Look up simulation rules** | `lab/docs/simulation-rules.md` |
| **Understand the experiment pipeline** | `lab/workflows/CONTEXT.md` |
| **Run holdout validation** | `bench/CONTEXT.md` |
| **Stress test a strategy** | `bench/CONTEXT.md` |
| **Read statistical gates** | `bench/docs/statistical-gates.md` |
| **Generate deployment C++** | `deploy/CONTEXT.md` |
| **Check live performance** | `deploy/CONTEXT.md` |
| **Look up instrument constants** | `_config/instruments.md` |
| **Check period windows and roles** | `_config/period-config.md` |
| **Review experiment history** | `audit/audit_log.md` |

---

## Cross-Workspace Flow

```
lab (Python + C++ experiments → frozen params + verified study)
    ↓ frozen configs + verified .cpp copy to bench/output/
bench (validate holdout → stress test → verdict)
    ↓ approved build copies to deploy/output/
deploy (live monitoring only)
```

lab never needs to know about deployment details.
deploy never needs to know about experiment pipelines.
bench reads frozen outputs from lab but never edits lab code.

**Handoff protocol:** When files cross workspace boundaries, copy
them to the destination workspace's output/ folder. The source
file stays in place. Each workspace reads only from its own
output/ or from _config/.

---

## File Discipline

Files belong in the workspace where they're used:
- Experiment code and results → lab/
- Holdout runs and verdicts → bench/
- Monitoring and drift → deploy/

If you're about to create a file in a workspace that doesn't
match the file placement rules, stop and check this file.

**Before finishing a session:** If you created a new file type,
added a skill, or changed a workflow:
1. Add the new type to this file's type catalog and placement rules
2. Update the workspace CONTEXT.md to reflect the change

---

## Automated Enforcement

These rules are enforced by git pre-commit hook:
- **Holdout lock:** blocks commits modifying locked holdout files
- **Audit log:** blocks deletions from `audit/audit_log.md`
- **Pickle guard:** blocks `.pkl`/`.pickle` commits (scoring models excepted)
- **Recalibration warning:** flags hardcoded thresholds (non-blocking)

---

## ID & Naming Conventions

<!--
Naming conventions belong in CLAUDE.md because they apply EVERYWHERE.
Any agent creating a file needs these rules, regardless of which
workspace it's in. The naming pattern IS the organization — no
separate folders needed per archetype or instrument.
-->

### File Name Pattern

`[arch]-[instrument]-[type]-[status].[ext]`

| Segment | Values | Required |
|---------|--------|----------|
| `arch` | `rotational`, `zone-touch`, or new slug | yes |
| `instrument` | `NQ`, `ES`, `GC`, or new from `_config/instruments.md` | yes |
| `type` | see type catalog below | yes |
| `status` | `draft`, `frozen`, `validated`, `deployed` | when applicable |
| `ext` | `.py`, `.json`, `.tsv`, `.csv`, `.md`, `.cpp` | yes |

### Type Catalog

| Type Slug | What It Is |
|-----------|-----------|
| `feature-engine` | Feature computation code |
| `simulator` | Strategy simulator (do not edit) |
| `evaluator` | Feature screening harness (do not edit) |
| `hypothesis-configs` | Parameter definitions |
| `trend-defense` | Risk filter |
| `results` | Experiment log (append-only) |
| `findings` | Analysis doc (numbered findings) |
| `features` | Feature set (status: frozen when locked) |
| `params` | Exit parameters (status: frozen when locked) |
| `hypothesis` | Hypothesis config (status: frozen when promoted) |
| `holdout-tradelog` | Holdout run output (window-tagged) |
| `stress-montecarlo` | Monte Carlo stress test |
| `stress-slippage` | Slippage sweep |
| `stress-kelly` | Kelly criterion / ruin prob |
| `verdict` | Statistical pass/fail |
| `study` | C++ ACSIL study implementation |
| `verify-report` | Cross-language verification report |
| `build` | Deployment C++ (verified, ready for production) |
| `deployment-checklist` | Human deployment verification checklist |
| `paper-trades` | Live paper trade log |
| `drift` | Drift monitoring report |
| `journal` | Strategy progression narrative (append-only) |

**Statuses:** `draft` → `frozen` → `validated` → `deployed`

### Before creating any file

1. Filename matches `[arch]-[instrument]-[type]-[status].[ext]`
2. Type exists in the catalog above
3. File lands in the correct workspace per placement rules below
4. If type is NOT in the catalog, flag it before creating

### Adding a new archetype or instrument

No folders to create. Register in `_config/instruments.md`, start
naming files. For new archetypes: clone an existing simulator and
evaluator as starting point, adapt to new strategy mechanics.

---

## File Placement Rules

### Lab (experiments)
- **Feature engines:** `lab/[arch]-[inst]-feature-engine.py`
- **Simulators:** `lab/[arch]-[inst]-simulator.py`
- **Evaluators:** `lab/[arch]-[inst]-evaluator.py`
- **Hypothesis configs:** `lab/[arch]-[inst]-hypothesis-configs.py`
- **Risk filters:** `lab/[arch]-[inst]-trend-defense.py`
- **C++ studies:** `lab/[arch]-[inst]-study.cpp`
- **Experiment results:** `lab/output/[arch]-[inst]-results.tsv`
- **Findings:** `lab/output/[arch]-[inst]-findings.md`
- **Frozen features:** `lab/output/[arch]-[inst]-features-frozen.json`
- **Frozen params:** `lab/output/[arch]-[inst]-params-frozen.json`
- **Frozen hypothesis:** `lab/output/[arch]-[inst]-hypothesis-frozen.json`
- **Journal:** `lab/output/[arch]-[inst]-journal.md`
- **Verify reports:** `lab/output/[arch]-[inst]-verify-report.md`
- **Ready for validation:** Copy frozen configs to `bench/output/`

### Bench (validation)
- **Holdout trade log:** `bench/output/[arch]-[inst]-holdout-tradelog-[window].csv`
- **Holdout lock:** `bench/output/holdout-locked-[arch]-[inst]-[window].flag`
- **Stress tests:** `bench/output/[arch]-[inst]-stress-[type]-[window].md`
- **Verdicts:** `bench/output/[arch]-[inst]-verdict-[window]-validated.json`
- **Ready for deployment:** Copy verdict + frozen params to `deploy/output/`

### Deploy (build & monitor)
- **C++ builds:** `deploy/output/[arch]-[inst]-build-v[n]-deployed.cpp`
- **Paper trades:** `deploy/output/[arch]-[inst]-paper-trades.csv`
- **Drift reports:** `deploy/output/[arch]-[inst]-drift-[YYYY-MM].md`
