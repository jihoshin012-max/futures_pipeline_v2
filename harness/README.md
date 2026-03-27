# Harness

Shared code that multiple archetypes use. Starts empty.

**When you add or change a file in this directory, update this README.**

## When code moves here

When you find yourself duplicating logic across archetypes —
loading data, applying cost models, computing metrics, running
backtests — extract the common parts here.

Once code lives in harness/, it becomes fixed. Don't edit it
during experiments. That's what makes results comparable across
archetypes and across time.

## How it gets used

Lab stage contracts reference harness engines as skills. When
an engine exists here, the stage contract specifies:
- What to call
- What arguments to pass
- What it returns

Until an engine is extracted, archetypes run their own code
directly. The scaffold works either way.

## Updating harness code

If a harness engine needs to change (bug fix, new capability):
1. Make the change
2. Log to `audit/audit_log.md`
3. Re-run affected experiments to verify results still hold
4. Update any stage contracts that reference changed behavior
