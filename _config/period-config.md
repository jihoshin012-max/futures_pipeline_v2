# Period Configuration

## How Periods Work

Data is organized into **windows** — fixed date ranges with permanent
names. Each window is assigned a **role** that determines how it can
be used. When new data arrives, roles shift — but windows never
change or rename.

## Windows

| Window | Start | End | Role |
|--------|-------|-----|------|
| W1 | — | — | calibration |
| W2 | — | — | calibration |
| W3 | — | — | holdout |

<!--
Add new windows as data becomes available. Fill in dates.
The most recent complete quarter is typically holdout.
-->

## Roles

| Role | What it means | Agent rules |
|------|--------------|-------------|
| `calibration` | Free to use for search, iteration, tuning | Run as many experiments as budget allows |
| `holdout` | One-shot validation. Frozen params only. | Check lock flag before running. Lock after. |
| `future` | Not yet available. | Do not reference in any experiment. |

## Rolling Protocol

When a new data window becomes available:

1. The current `holdout` window → change role to `calibration`
2. Add the new window → assign role `holdout`
3. Update the table above (one edit)
4. Old holdout lock flags are now irrelevant — that window is
   calibration now, free to use
5. New holdout window has no lock flag — available for one-shot run
6. Log the roll to `audit/audit_log.md`

**Nothing else changes.** No file renames, no folder restructuring.
The agent reads this config and knows which window is which role.

## Replication Split

For replication gating, calibration windows are split:
- **Replica-A:** First half of all calibration windows combined
- **Replica-B:** Second half of all calibration windows combined

Hypotheses must pass on both halves before advancing.

## Replication Gate Mode

| Mode | Behavior |
|------|----------|
| `hard_block` | Replica-B failure → auto-revert |
| `flag_and_review` | Replica-B failure → kept with weak flag |

**Current mode:** `hard_block`
