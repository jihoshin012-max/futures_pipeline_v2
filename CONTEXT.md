# Futures Pipeline — Task Router

<!--
last_reviewed: 2026-03-27
review_cadence: quarterly

====================================================================
This is LAYER 1 — THE ROUTER.

This file does ONE job: route agents to the right workspace.
It should be SHORT. 30-50 lines of actual content.

Rules for this file:
  - No detailed instructions (workspace CONTEXT.md handles that)
  - No file placement rules (CLAUDE.md handles that)
  - No naming conventions (CLAUDE.md handles that)
  - Just: "What's your task? → Go here. You'll also need X."

The "You'll Also Need" column is critical. It tells agents what
CROSS-WORKSPACE resources to pull.
====================================================================
-->

## What This Is

Ji's futures strategy research pipeline. Three siloed workspaces,
each handling one part of the strategy lifecycle.

**CLAUDE.md** (always loaded) has the full folder map and naming
rules. This file routes you to work.

**Resuming work?** Name an archetype+instrument. The agent checks
what files exist across lab/output/, bench/output/, deploy/output/
to report current state.

**Periodic health check:** Ask the agent to review for recurring
patterns. This can be scoped to an archetype (read its journal
and results) or scaffold-wide (read audit log, check CONTEXT.md
files against actual usage, look for process friction). When a
pattern emerges, fix the source file directly.

---

## Task Routing

| Your Task | Go Here | You'll Also Need |
|-----------|---------|------------------|
| **Screen features** | `lab/CONTEXT.md` | `_config/instruments.md` |
| **Test hypotheses** | `lab/CONTEXT.md` | `_config/period-config.md` |
| **Sweep parameters** | `lab/CONTEXT.md` | `lab/docs/simulation-rules.md` |
| **Analyze results** | `lab/CONTEXT.md` | `audit/audit_log.md` for history |
| **Run holdout validation** | `bench/CONTEXT.md` | Frozen params from `lab/output/`, `_config/period-config.md` |
| **Stress test** | `bench/CONTEXT.md` | `bench/docs/statistical-gates.md` |
| **Get verdict** | `bench/CONTEXT.md` | Holdout tradelog + stress reports from `bench/output/` |
| **Develop C++ study** | `lab/CONTEXT.md` | ACSIL workspace (`C:\Projects\sierrachart`) |
| **Verify Python ↔ C++ match** | `lab/workflows/verify/CONTEXT.md` | Both implementations must exist |
| **Monitor live** | `deploy/CONTEXT.md` | Verdict from `bench/output/` |
| **Understand experiment pipeline** | `lab/workflows/CONTEXT.md` | — |
| **Look up instruments** | `_config/instruments.md` | — |
| **Check period windows** | `_config/period-config.md` | — |
| **Review audit trail** | `audit/audit_log.md` | — |

---

## Workspace Summary

| Workspace | Purpose | Skills & Tools |
|-----------|---------|----------------|
| `lab/` | Python + C++ experiments → frozen configs + verified builds. | Harness engines, ACSIL compiler, `/autoresearch` |
| `bench/` | Validation → verdicts. Holdout, stress test, assessment. | `/fractal_monitor` |
| `deploy/` | Live monitoring. Paper trade, drift detection. | — |

Each workspace has its own CONTEXT.md with full details. Read that
when working in a workspace, not this file.

---

## Cross-Workspace Flow

```
lab (Python + C++ experiments → frozen params + verified study)
    ↓ frozen configs + verified .cpp copy to bench/output/
bench (validate holdout → stress test → verdict)
    ↓ approved build copies to deploy/output/
deploy (live monitoring only)
```

<!--
This diagram appears in both CLAUDE.md and CONTEXT.md.
That's intentional — CLAUDE.md shows it as part of the permanent map,
CONTEXT.md shows it as part of routing context. The duplication is
small (4 lines) and serves different readers (the always-loaded map
vs. the task-specific router).
-->
