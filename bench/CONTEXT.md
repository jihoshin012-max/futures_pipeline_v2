# Bench

last_reviewed: 2026-03-27 | review_cadence: quarterly

## What This Workspace Is

The proving ground. Strategies arrive here with frozen parameters
from lab. Bench runs them against the current holdout period,
stress tests robustness, and produces a pass/fail verdict.
This is **downstream** of lab, **upstream** of deploy:

- Reads frozen configs from lab/output/ (never edits lab code)
- Reads `_config/period-config.md` to find the current holdout date range
- Approved strategies flow to deploy/ for monitoring

---

## Hard Rules

1. **Holdout is one shot per date range.** Never re-run the same
   holdout period with different params. The lock flag is permanent.
2. **Audit log is append-only.** Never delete or modify entries.
3. **Verdicts are deterministic.** Same inputs → same output. No
   subjective adjustments.
4. **Never edit lab code from bench.** Read frozen outputs only.

---

## Where to Go

| You Want To... | Go Here |
|----------------|---------|
| **Run holdout validation** | See Holdout section below |
| **Stress test** | See Stress Test section below |
| **Produce verdict** | See Verdict section below |
| **Look up statistical gates** | `docs/statistical-gates.md` |
| **Check holdout dates** | `_config/period-config.md` |

**Don't read everything.** Identify your task, load only what you need.

---

## Folder Structure

```
bench/
├── CONTEXT.md                                                  ← You are here
├── docs/                                                       ← Layer 3: reference
│   └── statistical-gates.md                                       PF thresholds, MWU, permutation gates
└── output/                                                     ← Layer 4: working artifacts
    ├── [arch]-[inst]-holdout-tradelog-[YYYYMMDD-YYYYMMDD].csv     Holdout trade log
    ├── [arch]-[inst]-stress-[type]-[YYYYMMDD-YYYYMMDD].md         Stress test reports
    ├── [arch]-[inst]-verdict-[YYYYMMDD-YYYYMMDD]-validated.json   Final verdict
    └── holdout-locked-[arch]-[inst]-[YYYYMMDD-YYYYMMDD].flag      Lock (this period is done)
```

---

## What to Load

| Task | Layer 3 (internalize as rules) | Layer 4 (process as input) | Skip |
|------|-------------------------------|---------------------------|------|
| Holdout run | `docs/statistical-gates.md`, `_config/instruments.md`, `_config/period-config.md` | Frozen params from `lab/output/` | All lab code, deploy |
| Stress test | `docs/statistical-gates.md` | Holdout tradelog for current period | Lab, deploy |
| Verdict | `docs/statistical-gates.md`, `_config/regime-definitions.md` | Holdout tradelog + stress reports for same period | Lab, deploy |

---

## Holdout Validation

**This runs exactly once per archetype+instrument+holdout period. One shot.**

1. Read `_config/period-config.md` — find the holdout date range
   (e.g., 2025-12-15 to 2026-03-14 → `20251215-20260314`)
2. Check: does `output/holdout-locked-[arch]-[inst]-20251215-20260314.flag` exist?
   - If yes: **STOP.** This holdout period is done.
   - If no: proceed.
3. Read frozen params from `bench/output/[arch]-[inst]-params-frozen.json`
   (copied from lab/output/ during cross-workspace handoff)
4. Run: `python harness/backtest_engine.py --config [frozen-params] --holdout-start [start] --holdout-end [end]`
5. Write trade log to `output/[arch]-[inst]-holdout-tradelog-20251215-20260314.csv`
6. Create `output/holdout-locked-[arch]-[inst]-20251215-20260314.flag`
7. Log to `audit/audit_log.md`

**When periods roll:** New holdout date range means a fresh shot.
Old lock flags stay forever — they're tagged to a specific date
range that will never be holdout again. History is preserved.

---

## Stress Test

After holdout validation, run robustness checks against the holdout trade log:

- **Monte Carlo:** Bootstrap resampling of trade sequence
- **Slippage:** Sweep cost assumptions from 1t to 5t
- **Kelly:** Kelly criterion sizing + ruin probability
- **Prop firm sim:** Drawdown limits, profit targets, evaluation periods

Each produces: `output/[arch]-[inst]-stress-[type]-[YYYYMMDD-YYYYMMDD].md`

---

## Verdict

Deterministic computation. No judgment calls — just gates.

1. Read holdout trade log + all stress reports for the same period
2. Apply gates from `docs/statistical-gates.md` (PF, MWU p-value,
   permutation p-value, percentile rank, drawdown limits)
3. Write `output/[arch]-[inst]-verdict-[YYYYMMDD-YYYYMMDD]-validated.json`
4. Write `output/[arch]-[inst]-verdict-[YYYYMMDD-YYYYMMDD]-report.md` (human-readable)
5. Log to `audit/audit_log.md`

Gate result is PASS or FAIL. No partial credit.

### Handoff to Deploy

If verdict is PASS: copy `bench/output/[arch]-[inst]-params-frozen.json`
and `bench/output/[arch]-[inst]-verdict-[YYYYMMDD-YYYYMMDD]-validated.json`
to `deploy/output/`. If verdict is FAIL, nothing moves to deploy.

---

## Skills & Tools for This Workspace

| Skill / Tool | Activation | When | Purpose |
|-------------|-----------|------|---------|
| `docs/statistical-gates.md` | ALWAYS-ON | Every validation task | Internalize pass/fail thresholds |
| `_config/period-config.md` | ALWAYS-ON | Every validation task | Know holdout date range |
| `harness/backtest_engine.py` | STAGE TRIGGER | Holdout run | Run frozen config against holdout data |
| `_config/instruments.md` | STAGE TRIGGER | Holdout run | Tick size, cost for PF calculation |
| `/fractal_monitor` | ON-DEMAND | Post-holdout | NQ fractal structure analysis |

### Skills You Might Add

- **Monte Carlo sim skill** — automated bootstrap stress testing
- **Kelly calculator** — position sizing + ruin probability computation
- **Prop firm evaluator** — simulate drawdown/profit rules for specific firms
- **Regime comparison** — compare calibration vs holdout regime distributions
