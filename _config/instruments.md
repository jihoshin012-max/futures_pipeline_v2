# Instrument Registry

All agents read constants from this file. Never hardcode tick size,
tick value, cost_ticks, or session times.

## NQ (Nasdaq 100 E-mini)

| Constant | Value |
|----------|-------|
| tick_size | 0.25 |
| tick_value | 5.00 |
| cost_ticks | 2.0 |
| session_start | 18:00 ET |
| session_end | 17:00 ET |
| rth_start | 09:30 ET |
| rth_end | 16:00 ET |

## ES (S&P 500 E-mini)

| Constant | Value |
|----------|-------|
| tick_size | 0.25 |
| tick_value | 12.50 |
| cost_ticks | 2.0 |
| session_start | 18:00 ET |
| session_end | 17:00 ET |
| rth_start | 09:30 ET |
| rth_end | 16:00 ET |

## GC (Gold)

| Constant | Value |
|----------|-------|
| tick_size | 0.10 |
| tick_value | 10.00 |
| cost_ticks | 2.0 |
| session_start | 18:00 ET |
| session_end | 17:00 ET |
| rth_start | 09:30 ET |
| rth_end | 13:30 ET |

---

## Experiment Defaults

| Constant | Value | Used by |
|----------|-------|---------|
| experiment_cost_ticks | 3 | All stages — PF computed at this cost assumption |
| min_trades | 30 | Stages 02, 03 — minimum trade count for valid result |

---

## Adding a New Instrument

Copy a section above, update the values. All agents will pick up
the new instrument through the naming convention:
`[arch]-[NEW-INST]-[type]-[status].[ext]`
