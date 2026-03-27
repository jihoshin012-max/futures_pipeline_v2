# Deploy

last_reviewed: 2026-03-27 | review_cadence: quarterly

## What This Workspace Is

Live monitoring station. Verified strategies arrive here with a
PASS verdict from bench. Deploy receives the final C++ build and
monitors paper trading and drift. This is the **final downstream**
workspace:

- Reads verified build + verdict from bench/output/
- Never reads lab code or bench internals
- No code changes happen here — observation only

---

## Hard Rules

1. **Human compiles, replays, creates flag.** Agent never creates
   `deployment-ready` flags.
2. **No code changes in this workspace.** Observation only.
3. **Never edit lab or bench files from deploy.**
4. **Verdict must be PASS.** Do not deploy a FAIL verdict.

---

## Where to Go

| You Want To... | Go Here |
|----------------|---------|
| **Start monitoring a strategy** | See Monitor section below |
| **Check for drift** | See Drift section below |
| **Look up trigger thresholds** | `docs/triggers.md` |

---

## Folder Structure

```
deploy/
├── CONTEXT.md                                       ← You are here
├── docs/                                            ← Layer 3: reference
│   └── triggers.md                                     Monitoring thresholds, escalation rules
└── output/                                          ← Layer 4: working artifacts
    ├── [arch]-[inst]-build-v[n]-deployed.cpp            Verified C++ build
    ├── [arch]-[inst]-deployment-checklist.md             Human verification checklist
    ├── [arch]-[inst]-paper-trades.csv                   Paper trade log
    ├── [arch]-[inst]-drift-[YYYY-MM].md                 Monthly drift report
    └── deployment-ready-[arch]-[inst].flag               Human creates this
```

---

## What to Load

| Task | Layer 3 (internalize as rules) | Layer 4 (process as input) | Skip |
|------|-------------------------------|---------------------------|------|
| Start monitoring | `docs/triggers.md`, `_config/instruments.md` | Verified build + verdict from `bench/output/` | Lab, bench internals |
| Drift check | `docs/triggers.md` | `output/[arch]-[inst]-paper-trades.csv`, prior drift reports | Lab, bench |

---

## Skills & Tools for This Workspace

| Skill / Tool | Activation | When | Purpose |
|-------------|-----------|------|---------|
| `docs/triggers.md` | ALWAYS-ON | All monitoring | Internalize trigger thresholds and escalation rules |
| `_config/instruments.md` | ALWAYS-ON | All monitoring | Tick size for PnL calculation |

### Skills You Might Add

- **Drift monitor** — scheduled paper trade comparison against verdict baseline
- **Alert notifier** — trigger when drift exceeds threshold
- **Screenshot skill** — capture SC chart output for documentation

---

## Deployment Checklist (human completes)

When a verified build arrives from bench:

- [ ] C++ review: no magic numbers, params match frozen config
- [ ] Compilation: no warnings
- [ ] Replay verification: matches backtest results
- [ ] Audit entry: logged to `audit/audit_log.md`
- [ ] Create `output/deployment-ready-[arch]-[inst].flag`

---

## Monitor

Ongoing observation. No code changes.

- Import paper trade data to `output/[arch]-[inst]-paper-trades.csv`
- Compare against `bench/output/[arch]-[inst]-verdict-[window]-validated.json`
  baseline (PF, win rate, drawdown, trade frequency)
- Produce `output/[arch]-[inst]-drift-[YYYY-MM].md` monthly

---

## Drift

If drift detected (PF degradation, win rate compression, drawdown
expansion beyond baseline tolerance):

1. Flag in `audit/audit_log.md`
2. Write drift report with evidence
3. Human decides: continue, pause, or kill
