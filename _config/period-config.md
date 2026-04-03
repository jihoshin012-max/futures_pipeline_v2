# Period Configuration

## How Periods Work

All data before the holdout start date is calibration (IS).
The holdout window is always the most recent ~3 months.
When new data arrives, the old holdout becomes calibration
and the new data becomes holdout. One edit.

## Current Periods

| Role | Start | End |
|------|-------|-----|
| calibration | 2021-10-01 | 2025-12-14 |
| holdout | 2025-12-17 | 2026-03-13 |

## Rolling Protocol

When new quarterly data arrives:
1. Change holdout start/end to the new quarter
2. Calibration end extends to the old holdout end
3. Calibration start stays the same (IS grows)
4. Log to audit/audit_log.md

**That's it.** No window renaming, no file renaming, no role tables.
Calibration grows automatically because "everything before holdout"
includes more data each quarter.

## Replication Split

For replication gating, calibration date range is split at midpoint:
- **Replica-A:** First half of calibration range
- **Replica-B:** Second half of calibration range

Hypotheses must pass on both halves before advancing.

## Replication Gate Mode

| Mode | Behavior |
|------|----------|
| `hard_block` | Replica-B failure → auto-revert |
| `flag_and_review` | Replica-B failure → kept with weak flag |

**Current mode:** `hard_block`
