# Lab Workflows

## What This Is

Routes to the right work mode. Lab has two kinds of development
and one verification gate.

---

## How Work Happens Here

### Systematic Python pipeline (stages 01 → 02 → 03)

Sequential. One experiment at a time. Harness-driven. Produces
frozen configs. Use this for rigorous calibration and screening.

| Stage | What Happens | Reads From | Produces |
|-------|-------------|------------|----------|
| **01-features** | Screen features on calibration data | New archetype code | `features-frozen.json` |
| **02-hypotheses** | Test hypothesis configs on calibration data | Frozen features from 01 | `hypothesis-frozen.json` |
| **03-params** | Optimize exit params on calibration data | Promoted hypothesis from 02 | `params-frozen.json` |

Each stage has its own CONTEXT.md with an Inputs/Process/Outputs
contract. Read the stage CONTEXT.md before starting work.

### C++ development (iterative, not staged)

Edit, compile, replay, compare, iterate. Can happen before, during,
or after the Python pipeline. See "C++ Development" section in
`lab/CONTEXT.md` for process and tools.

### Cross-language verification (stage 04 — optional)

Run when both `.py` and `.cpp` implementations exist for the same
archetype+instrument. Must pass before handoff to bench.

---

## Flow

```
01-features
    ↓ human approves → features-frozen.json
02-hypotheses
    ↓ human approves → hypothesis-frozen.json
03-params
    ↓ human approves → params-frozen.json
    ↓
verify (if C++ exists)
    ↓ Python ↔ C++ match confirmed
    ↓ ready for bench/ validation
```

C++ development can happen at any point alongside this flow.
Stage 04 is where Python and C++ must align before leaving lab.

**Human gates between every stage.** No automatic promotion.

---

## Shared Rules (apply to all stages)

- One change per experiment (Python pipeline)
- Log every run to `lab/output/[arch]-[inst]-results.tsv`
- Keep or revert based on stage-specific metric and threshold
- Constants always from `_config/instruments.md`
- Journal entry for every significant decision
