# Lab

last_reviewed: 2026-03-27 | review_cadence: quarterly

## What This Workspace Is

The experiment shop. All strategy research happens here — Python
systematic pipeline, C++ iterative development, and cross-language
verification. Both Python and C++ code live here as flat files
identified by naming convention. This is **upstream** of everything:

- Frozen params + verified C++ flow to bench/ for validation
- Lab never reads from bench/ or deploy/

---

## Hard Rules

1. **One change per experiment.** Edit one parameter, run harness, read
   result, keep or revert, log to results.tsv. Never batch changes.
2. **Entry-time only.** Features must be computable at entry bar.
   No future data. Evaluator enforces this — violations auto-revert.
3. **Replication gate.** Hypotheses must pass on both Replica-A and
   Replica-B halves of calibration data. Driver enforces this.
4. **Constants from registry.** Read tick size, cost_ticks, sessions
   from `_config/instruments.md`. Never hardcode.
5. **Never edit harness files.** `backtest_engine.py`,
   `evaluate_features.py`, `hypothesis_generator.py` are fixed.
6. **Never edit simulators.** `[arch]-[inst]-simulator.py` files are
   fixed harness code.
7. **Archetype header.** Line 1 of every archetype .py file:
   `# archetype: {name}`
8. **Audit everything.** Every experiment logged to results.tsv.
   Every promotion logged to `audit/audit_log.md`.
9. **Journal every decision.** Append to `output/[arch]-[inst]-journal.md`
   when: freezing configs, rejecting a hypothesis, changing approach,
   noting a weak spot, or returning from a failed holdout/drift finding.
   Create the journal on first entry. The journal is the strategy's
   memory. If it's not journaled, the next session won't know why
   a decision was made.

---

## What to Load

**Always load first:** `output/[arch]-[inst]-journal.md` (if exists).
The journal carries the full narrative. Read it before starting any task.

| Task | Layer 3 (internalize) | Layer 4 (process) | Skip |
|------|----------------------|-------------------|------|
| Feature screening | `docs/feature-rules.md`, `_config/instruments.md` | `output/[arch]-[inst]-results.tsv` | hypothesis-configs, exit-templates, C++ code, other archetypes |
| Hypothesis testing | `docs/simulation-rules.md`, `_config/period-config.md` | `output/[arch]-[inst]-features-frozen.json` | feature code, C++ code, other archetypes |
| Param sweep | `docs/exit-templates.md`, `docs/simulation-rules.md` | `output/[arch]-[inst]-results.tsv` | feature code, C++ code, other archetypes |
| C++ development | `docs/simulation-rules.md` | `[arch]-[inst]-study.cpp`, `output/[arch]-[inst]-params-frozen.json` (if exists) | Python harness code, other archetypes |
| Cross-language verify | `_config/instruments.md` | Both `.py` and `.cpp`, `output/[arch]-[inst]-params-frozen.json` | other archetypes |
| Analyze results | — | `output/[arch]-[inst]-results.tsv` | all code files |

---

## Where to Go

| You Want To... | Go Here |
|----------------|---------|
| **Prepare data for experiments** | `workflows/dataprep/` |
| **Run Python experiments** | `workflows/CONTEXT.md` |
| **Develop/iterate C++ study** | See "C++ Development" below |
| **Verify Python ↔ C++ match** | `workflows/verify/CONTEXT.md` |
| **Look up simulation rules** | `docs/simulation-rules.md` |
| **Look up feature rules** | `docs/feature-rules.md` |
| **Look up exit templates** | `docs/exit-templates.md` |
| **Look up ACSIL build rules** | ACSIL workspace: `C:\Projects\sierrachart\reference\` |
| **Look up results.tsv format** | `docs/results-schema.md` |
| **See past results** | `output/[arch]-[inst]-results.tsv` |
| **Read strategy journal** | `output/[arch]-[inst]-journal.md` |

**Don't read everything.** Identify your task, load only what you need.

---

## Skills & Tools

| Skill / Tool | Activation | When | Purpose |
|-------------|-----------|------|---------|
| `docs/simulation-rules.md` | ALWAYS-ON | Every experiment | Internalize entry/exit/trail/cost mechanics |
| `_config/instruments.md` | ALWAYS-ON | Every experiment | Tick size, cost, session times — never hardcode |
| `docs/feature-rules.md` | STAGE TRIGGER | features | Valid features, entry-time constraint |
| `docs/exit-templates.md` | STAGE TRIGGER | params | Exit patterns during param optimization |
| `/autoresearch` | STAGE TRIGGER | 01, 02, 03 | Orchestrate edit-run-evaluate-log loop |
| `harness/evaluate_features.py` | STAGE TRIGGER | features | Python: fixed evaluator — bin spread |
| `harness/hypothesis_generator.py` | STAGE TRIGGER | hypotheses | Python: fixed generator — calibration + replica |
| `harness/backtest_engine.py` | STAGE TRIGGER | params | Python: fixed engine — PF |
| `/build-study` | ON-DEMAND | C++ build/iterate | Gathers frozen params + config from pipeline, compiles at SC workspace, places .cpp in lab/, logs to journal + audit |
| ACSIL workspace (`C:\Projects\sierrachart`) | ON-DEMAND | C++ development (manual) | Direct access for hands-on C++ work outside the build-study flow |
| Replay comparison | STAGE TRIGGER | verify | Compare Python vs C++ output |
| `/zone-data-prep` | ON-DEMAND | Pre-01 | Prepare zone touch data |

---

## C++ Development

C++ work is iterative, not staged. It can happen before, during,
or after the Python pipeline.

**Build via skill (primary):** Run `/build-study` with archetype +
instrument. The skill gathers frozen params, instrument config, and
simulation rules from the pipeline, compiles at `C:\Projects\sierrachart`,
places the .cpp in `lab/` with correct naming, and logs to journal +
audit. You stay in this workspace the whole time.

**Manual ACSIL session (fallback):** Open a session directly in the
ACSIL workspace (`C:\Projects\sierrachart`) for hands-on C++ work —
prototyping, debugging, or explorative iteration outside the standard
flow. The ACSIL agent reads this scaffold's CLAUDE.md for naming rules
and writes `lab/[arch]-[inst]-study.cpp` directly here.

**Starting from C++:** Prototype in SC until behavior looks right.
Then translate to Python for systematic pipeline (01-02-03).
Port refinements back to C++.

**Starting from Python:** Run pipeline to frozen params. Use
`/build-study` to translate to study.cpp. Compile and replay.
Fix discrepancies until match.

**When both exist:** Run `workflows/verify/` before handoff to bench.

---

## Folder Structure

```
lab/
├── CONTEXT.md                              ← You are here
├── docs/                                   ← Layer 3: reference (load per-task)
│   ├── simulation-rules.md
│   ├── feature-rules.md
│   ├── exit-templates.md
│   └── results-schema.md
├── [arch]-[inst]-feature-engine.py         ← Python: edit targets
├── [arch]-[inst]-simulator.py              ← Python: fixed (do not edit)
├── [arch]-[inst]-evaluator.py              ← Python: fixed (do not edit)
├── [arch]-[inst]-hypothesis-configs.py     ← Python: edit targets
├── [arch]-[inst]-trend-defense.py          ← Python: edit targets
├── [arch]-[inst]-study.cpp                 ← C++: ACSIL study (iterative)
├── output/                                 ← Layer 4: working artifacts
│   ├── [arch]-[inst]-results.tsv
│   ├── [arch]-[inst]-findings.md
│   ├── [arch]-[inst]-features-frozen.json
│   ├── [arch]-[inst]-params-frozen.json
│   ├── [arch]-[inst]-hypothesis-frozen.json
│   ├── [arch]-[inst]-journal.md
│   └── [arch]-[inst]-verify-report.md
└── workflows/
    ├── CONTEXT.md
    ├── dataprep/
    ├── features/
    ├── hypotheses/
    ├── params/
    └── verify/
```
