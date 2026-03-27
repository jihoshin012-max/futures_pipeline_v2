# Monitoring Triggers

<!--
Layer 3 reference. Loaded by deploy agents during paper trade
monitoring and drift detection. Defines concrete thresholds for
when to flag issues and escalation rules.
-->

## Trigger Thresholds

| Trigger | Condition | Action |
|---------|-----------|--------|
| PF divergence | PF deviates > 40% from verdict baseline after 50+ trades | Flag in audit log, write drift report |
| Consecutive stops | 8 or more consecutive stop-outs | Flag in audit log, pause trading |
| Drawdown breach | Max drawdown exceeds 2x backtest baseline | Flag in audit log, pause trading |
| Trade frequency | Fewer than 5 trades per month | Flag in audit log, investigate |
| Win rate compression | Win rate drops > 15pp from baseline | Flag in audit log, write drift report |

## Escalation Rules

1. **Any trigger fires:** Create entry in `audit/audit_log.md` FIRST,
   before investigation
2. **Single trigger:** Write drift report, human reviews
3. **Multiple triggers on same strategy:** Pause paper trading,
   human decides continue/pause/kill
4. **Drawdown or consecutive stops:** Immediate pause, no waiting
   for human review

## Promotion Trigger

After 200+ paper trades with no trigger fires:
- Flag for potential IS promotion (new calibration data)
- Human decides whether to roll periods and re-validate
- Log decision to audit
