# Futures Pipeline — Workspace Map

last_reviewed: 2026-03-27 | review_cadence: quarterly

## What This Is

Ji's futures strategy research system. Three siloed workspaces —
lab, bench, deploy — each handling one part of the strategy
lifecycle. An agent drops into a workspace, reads its CONTEXT.md,
does its work, and exits.

**CONTEXT.md** (top-level) routes you to the right workspace.
This file is the map.

---

## File Discipline

Files belong in the workspace where they're used:
- Experiment code and results → lab/
- Holdout runs and verdicts → bench/
- Monitoring and drift → deploy/

If you're about to create a file in a workspace that doesn't
match the file placement rules, stop and check this file.

**Before creating any file:**
1. Filename matches `[arch]-[instrument]-[type]-[status].[ext]`
2. Type exists in the type catalog below
3. File lands in the correct workspace per placement rules below
4. If type is NOT in the catalog, flag it before creating

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

### File Name Pattern

`[arch]-[instrument]-[type]-[status].[ext]`

| Segment | Values | Required |
|---------|--------|----------|
| `arch` | `rotational`, `zone-touch`, or new slug | yes |
| `instrument` | `NQ`, `ES`, `GC`, or new from `_config/instruments.md` | yes |
| `type` | see type catalog below | yes |
| `status` | `draft`, `frozen`, `validated`, `deployed` | when applicable |
| `ext` | `.py`, `.json`, `.tsv`, `.csv`, `.md`, `.cpp`, `.h`, `.txt` | yes |

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
| `holdout-tradelog` | Holdout run output (date-range tagged) |
| `stress-montecarlo` | Monte Carlo stress test |
| `stress-slippage` | Slippage sweep |
| `stress-kelly` | Kelly criterion / ruin prob |
| `verdict` | Statistical pass/fail |
| `study` | C++ ACSIL strategy study (single variant use `study`, multiple variants append `-variant`) |
| `study-config` | Companion config for a study (.h or .txt — not compiled, human reference) |
| `zone-detector` | C++ zone detection study (study chain dependency) |
| `zone-detector-history` | C++ batch zone export study (study chain dependency) |
| `touch-engine` | C++ touch detection + feature computation study (study chain dependency) |
| `verify-report` | Cross-language verification report |
| `build` | Deployment C++ (verified, ready for production) |
| `deployment-checklist` | Human deployment verification checklist |
| `paper-trades` | Live paper trade log |
| `drift` | Drift monitoring report |
| `journal` | Strategy progression narrative (append-only) |

**Statuses:** `draft` → `frozen` → `validated` → `deployed`

### Adding a new archetype or instrument

No folders to create. Register in `_config/instruments.md`, start
naming files. For new archetypes: clone an existing simulator and
evaluator as starting point, adapt to new strategy mechanics.

---

## File Placement Rules

### Data (source material)
- **Naming:** `data/[instrument]-[source]-[role].csv` — see `data/README.md`
- Data files are source material, not pipeline artifacts. They use their
  own naming convention (no `[arch]` or `[status]` segments).
- All data files are gitignored.

### Lab (experiments)
- **Feature engines:** `lab/[arch]-[inst]-feature-engine.py`
- **Simulators:** `lab/[arch]-[inst]-simulator.py`
- **Evaluators:** `lab/[arch]-[inst]-evaluator.py`
- **Hypothesis configs:** `lab/[arch]-[inst]-hypothesis-configs.py`
- **Risk filters:** `lab/[arch]-[inst]-trend-defense.py`
- **C++ studies:** `lab/[arch]-[inst]-study.cpp` (or `study-[variant].cpp` for multiple variants)
- **Study configs:** `lab/[arch]-[inst]-study-[variant]-config.{h,txt}` (companion reference)
- **Study chain deps:** `lab/[arch]-[inst]-zone-detector.cpp`, `touch-engine.cpp`, etc.
- **Experiment results:** `lab/output/[arch]-[inst]-results.tsv`
- **Findings:** `lab/output/[arch]-[inst]-findings.md`
- **Frozen features:** `lab/output/[arch]-[inst]-features-frozen.json`
- **Frozen params:** `lab/output/[arch]-[inst]-params-frozen.json`
- **Frozen hypothesis:** `lab/output/[arch]-[inst]-hypothesis-frozen.json`
- **Journal:** `lab/output/[arch]-[inst]-journal.md`
- **Verify reports:** `lab/output/[arch]-[inst]-verify-report.md`
- **Ready for validation:** Copy frozen configs to `bench/output/`

### Bench (validation)
- **Holdout trade log:** `bench/output/[arch]-[inst]-holdout-tradelog-[YYYYMMDD-YYYYMMDD].csv`
- **Holdout lock:** `bench/output/holdout-locked-[arch]-[inst]-[YYYYMMDD-YYYYMMDD].flag`
- **Stress tests:** `bench/output/[arch]-[inst]-stress-[type]-[YYYYMMDD-YYYYMMDD].md`
- **Verdicts:** `bench/output/[arch]-[inst]-verdict-[YYYYMMDD-YYYYMMDD]-validated.json`
- **Ready for deployment:** Copy verdict + frozen params to `deploy/output/`

### Deploy (build & monitor)
- **C++ builds:** `deploy/output/[arch]-[inst]-build-v[n]-deployed.cpp`
- **Paper trades:** `deploy/output/[arch]-[inst]-paper-trades.csv`
- **Drift reports:** `deploy/output/[arch]-[inst]-drift-[YYYY-MM].md`

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
│   └── workflows/                         ← Experiment pipeline (3 stages + verify)
│
├── bench/                                 ← Validate and judge strategies
│   ├── CONTEXT.md
│   ├── docs/                              ← Statistical gates
│   └── output/                            ← Holdout trade logs, stress tests, verdicts
│
├── deploy/                                ← Monitor live strategies
│   ├── CONTEXT.md
│   ├── docs/                              ← Monitoring triggers
│   └── output/                            ← Verified builds, paper trades, drift
│
├── _config/                               ← Shared constants (read by all)
│   ├── instruments.md                        Tick size, tick value, sessions, costs
│   └── period-config.md                      Rolling windows + role assignments
│
├── infra/                                 ← Live infrastructure (signals for strategies)
│   ├── CONTEXT.md
│   └── blb/                              ← Consolidation detection (ML + shared memory)
│
├── harness/                               ← Shared engines (emerge from use, then fixed)
├── data/                                  ← Source market data (own naming: [inst]-[source]-[role].csv)
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
| **Monitor live performance** | `deploy/CONTEXT.md` |
| **Work on BLB consolidation detector** | `infra/CONTEXT.md` |
| **Look up instrument constants** | `_config/instruments.md` |
| **Check period windows and roles** | `_config/period-config.md` |
| **Onboard data or roll holdout** | `data/onboarding.md` |
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
