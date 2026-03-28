# Lab Workflows

## What This Is

Routes to the right work mode. Lab has two kinds of development
and one verification gate.

---

## How Work Happens Here

### Data preparation (dataprep)

Prepare raw data for baseline analysis and backtesting. Load,
filter, validate, join, and output clean files. Must run before
any experiment stages. Prompt specs live here as stage contracts.

### Systematic Python pipeline (features → hypotheses → params)

Sequential. One experiment at a time. Harness-driven. Produces
frozen configs. Use this for rigorous calibration and screening.

| Stage | What Happens | Reads From | Produces |
|-------|-------------|------------|----------|
| **features** | Screen features on calibration data | New archetype code | `features-frozen.json` |
| **hypotheses** | Test hypothesis configs on calibration data | Frozen features | `hypothesis-frozen.json` |
| **params** | Optimize exit params on calibration data | Promoted hypothesis | `params-frozen.json` |

Each stage has its own CONTEXT.md with an Inputs/Process/Outputs
contract. Read the stage CONTEXT.md before starting work.

### C++ development (iterative, not staged)

Edit, compile, replay, compare, iterate. Can happen before, during,
or after the Python pipeline. See "C++ Development" section in
`lab/CONTEXT.md` for process and tools.

### Cross-language verification (verify — optional)

Run when both `.py` and `.cpp` implementations exist for the same
archetype+instrument. Must pass before handoff to bench.

---

## Flow

```
dataprep
    ↓ clean data + baseline analysis
features
    ↓ human approves → features-frozen.json
hypotheses
    ↓ human approves → hypothesis-frozen.json
params
    ↓ human approves → params-frozen.json
    ↓
verify (if C++ exists)
    ↓ Python ↔ C++ match confirmed
    ↓ ready for bench/ validation
```

C++ development can happen at any point alongside this flow.
Verify is where Python and C++ must align before leaving lab.

**Human gates between every stage.** No automatic promotion.

---

## Shared Rules (apply to all stages)

- One change per experiment (Python pipeline, not dataprep)
- Log every run to `lab/output/[arch]-[inst]-results.tsv`
- Keep or revert based on stage-specific metric and threshold
- Constants always from `_config/instruments.md`
- Journal entry for every significant decision
