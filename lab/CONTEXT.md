# Lab

<!--
last_reviewed: 2026-03-27
review_cadence: quarterly

====================================================================
This workspace demonstrates the PIPELINE pattern.

Lab has two levels of CONTEXT.md:
  1. lab/CONTEXT.md (workspace entry point) — routes to docs or workflows
  2. lab/workflows/CONTEXT.md (pipeline entry point) — routes to stages

This is the most complex workspace in the system. It shows:
  - Sub-routing (workspace CONTEXT → pipeline CONTEXT)
  - Reference docs separate from workflow
  - Stage-specific tool integration
  - Layer 3 vs Layer 4 loading discipline
====================================================================
-->

## What This Workspace Is

The experiment shop. All strategy research happens here — Python
systematic pipeline, C++ iterative development, and cross-language
verification. Both Python and C++ code live here as flat files
identified by naming convention. This is **upstream** of everything:

- Frozen params + verified C++ flow to bench/ for validation
- Lab never reads from bench/ or deploy/

---

## Where to Go

| You Want To... | Go Here |
|----------------|---------|
| **Run Python experiments** | `workflows/CONTEXT.md` |
| **Develop/iterate C++ study** | See "C++ Development" below |
| **Verify Python ↔ C++ match** | `workflows/verify/CONTEXT.md` |
| **Look up simulation rules** | `docs/simulation-rules.md` |
| **Look up feature rules** | `docs/feature-rules.md` |
| **Look up exit templates** | `docs/exit-templates.md` |
| **Look up ACSIL build rules** | ACSIL workspace: `C:\Projects\sierrachart\reference\` |
| **Look up results.tsv format** | `docs/results-schema.md` |
| **See past results** | `output/[arch]-[inst]-results.tsv` |
| **Read findings** | `output/[arch]-[inst]-findings.md` |
| **Read strategy journal** | `output/[arch]-[inst]-journal.md` |

**Don't read everything.** Identify your task, load only what you need.

---

## Folder Structure

```
lab/
├── CONTEXT.md                              ← You are here
│
├── docs/                                   ← Layer 3: reference (load per-task)
│   ├── simulation-rules.md                    Entry, exit, trail, cost mechanics
│   ├── feature-rules.md                       What makes a valid feature
│   ├── exit-templates.md                      Reference exit structure patterns
│   ├── results-schema.md                      Column format for results.tsv
│                                                  (ACSIL reference is in external workspace)
│
├── [arch]-[inst]-feature-engine.py         ← Python: edit targets
├── [arch]-[inst]-simulator.py              ← Python: fixed (do not edit)
├── [arch]-[inst]-evaluator.py              ← Python: fixed (do not edit)
├── [arch]-[inst]-hypothesis-configs.py     ← Python: edit targets
├── [arch]-[inst]-trend-defense.py          ← Python: edit targets
├── [arch]-[inst]-study.cpp                 ← C++: ACSIL study (iterative)
│
├── output/                                 ← Layer 4: working artifacts
│   ├── [arch]-[inst]-results.tsv              Experiment log (append-only)
│   ├── [arch]-[inst]-findings.md              Analysis doc
│   ├── [arch]-[inst]-features-frozen.json     Locked feature set
│   ├── [arch]-[inst]-params-frozen.json       Locked exit parameters
│   ├── [arch]-[inst]-hypothesis-frozen.json   Promoted hypothesis
│   ├── [arch]-[inst]-journal.md               Strategy progression narrative
│   └── [arch]-[inst]-verify-report.md         Cross-language verification
│
└── workflows/                              ← Pipeline + verification
    ├── CONTEXT.md                             Routes: Python pipeline, C++ work, verify
    ├── 01-features/                           Python: screen features
    ├── 02-hypotheses/                         Python: test hypothesis configs
    ├── 03-params/                             Python: optimize exit params
    └── verify/                             Optional: verify Python ↔ C++ match
```

---

## What to Load

**Always load first:** `output/[arch]-[inst]-journal.md` (if exists). The
journal carries the strategy's full narrative — what was tried, what
worked, what failed, and why. Read it before starting any task.

| Task | Layer 3 (internalize as rules) | Layer 4 (process as input) | Skip |
|------|-------------------------------|---------------------------|------|
| Feature screening | `docs/feature-rules.md`, `_config/instruments.md` | `output/[arch]-[inst]-results.tsv` | hypothesis-configs, exit-templates, C++ code, other archetypes |
| Hypothesis testing | `docs/simulation-rules.md`, `_config/period-config.md` | `output/[arch]-[inst]-features-frozen.json` | feature code, C++ code, other archetypes |
| Param sweep | `docs/exit-templates.md`, `docs/simulation-rules.md` | `output/[arch]-[inst]-results.tsv` | feature code, C++ code, other archetypes |
| C++ development | `docs/simulation-rules.md` | `[arch]-[inst]-study.cpp`, `output/[arch]-[inst]-params-frozen.json` (if exists) | Python harness code, other archetypes. ACSIL reference is in external workspace. |
| Cross-language verify | `_config/instruments.md` | Both `.py` and `.cpp` implementations, `output/[arch]-[inst]-params-frozen.json` | other archetypes |
| Analyze results | — | `output/[arch]-[inst]-results.tsv` | all code files |

---

## Skills & Tools for This Workspace

<!--
Skills aren't listed generically. Each one has a WHEN and a WHY.
Three activation patterns:
  - STAGE TRIGGER: runs at a specific pipeline stage
  - ALWAYS-ON: applies to everything in this workspace
  - ON-DEMAND: available when the user asks for it

The CONTEXT.md is what makes a skill useful by telling the agent
when and why to invoke it — not just that it's available.

You can wire up to 15 skills per workspace. Plug them in where
needed rather than loading all at once.
-->

| Skill / Tool | Activation | When | Purpose |
|-------------|-----------|------|---------|
| `docs/simulation-rules.md` | ALWAYS-ON | Every experiment | Internalize entry/exit/trail/cost mechanics as constraints |
| `_config/instruments.md` | ALWAYS-ON | Every experiment | Tick size, cost, session times — never hardcode |
| `docs/feature-rules.md` | STAGE TRIGGER | 01-features | Defines valid features, entry-time constraint |
| `docs/exit-templates.md` | STAGE TRIGGER | 03-params | Reference exit patterns during param optimization |
| `/autoresearch` | STAGE TRIGGER | 01, 02, 03 | Orchestrate the edit-run-evaluate-log loop |
| `harness/evaluate_features.py` | STAGE TRIGGER | 01-features | Python: fixed evaluator — pass archetype, get bin spread |
| `harness/hypothesis_generator.py` | STAGE TRIGGER | 02-hypotheses | Python: fixed generator — pass config, get calibration + replica results |
| `harness/backtest_engine.py` | STAGE TRIGGER | 03-params | Python: fixed engine — pass config, get PF |
| ACSIL workspace (`C:\Projects\sierrachart`) | ON-DEMAND | C++ development | External agent — generates, compiles, and verifies C++ studies. Reads this scaffold's CLAUDE.md for naming rules. Writes study.cpp directly to lab/. |
| Replay comparison | STAGE TRIGGER | verify | Compare Python vs C++ output |
| `/zone-data-prep` | ON-DEMAND | Pre-01 | Prepare zone touch data before feature screening |

### Skills You Might Add

<!--
Skill slots are EXTENSIBLE. You don't need all 15 filled on day one.
These are suggestions for what you might add as your workflow matures.
-->

- **Results visualizer** — could run at end of any stage to plot PF distributions
- **Regime analyzer** — HMM breakdown per config, useful during 02-hypotheses
- **Feature scanner** — auto-suggest features from data patterns, useful pre-01
- **Experiment budget tracker** — warn when approaching 300/200/500 experiment limits

---

## C++ Development

C++ work is iterative, not staged. It can happen before, during,
or after the Python pipeline. There are no numbered stages for C++.

**Process:** Open a session in the ACSIL workspace (`C:\Projects\sierrachart`).
Tell it the archetype and instrument. It reads this scaffold's CLAUDE.md
for naming rules and writes `lab/[arch]-[inst]-study.cpp` directly here.
Compile via SC remote build → replay against calibration data → compare
behavior → iterate.

**Starting from C++:** If prototyping in Sierra Chart first, develop
the study.cpp until behavior looks right on replay. Then translate
logic to Python and run through the systematic pipeline (01-02-03)
for rigorous calibration. Port refinements back to C++.

**Starting from Python:** Run through the pipeline to frozen params.
Then translate to study.cpp. Compile and replay. Compare output
against Python baseline. Fix discrepancies until they match.

**When both exist:** Run `workflows/verify/` before handoff to bench.

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
