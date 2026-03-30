# Statistical Gates

<!--
Layer 3 reference. Loaded by bench agents during assessment.
Defines pass/fail thresholds for strategy validation.
All gates are deterministic — same inputs produce same verdict.
-->

## Gate Philosophy

Gates are minimum acceptable thresholds, not targets. A strategy
that barely passes all gates is deployable. A strategy that fails
one gate is not, regardless of how strong other metrics are.

Gates are set conservatively — they should reject bad strategies
without rejecting good ones that will degrade slightly in live.

---

## Gate Tiers

**HARD gates:** Fail = reject. No exceptions, no discretion. These
protect against deploying a fundamentally broken strategy.

**SOFT gates:** Fail = flag for review. May deploy with a monitoring
condition attached. The condition must be specific and measurable
(e.g., "halt if avg slippage exceeds 1t over 5 days").

---

## Hard Gates (fail = reject)

| # | Gate | Threshold | Rationale |
|---|------|-----------|-----------|
| H1 | Profit Factor | >= 1.20 | Must survive ~2t realistic slippage. Below 1.20 after slippage = breakeven or losing. |
| H2 | Minimum cycles | >= 5,000 | Statistical significance — fewer cycles = unreliable metrics. |
| H3 | Serial correlation | All lags 1-5 below 2/sqrt(N) | No dependency in trade sequence — ensures metrics aren't inflated by clustering. |
| H4 | Bootstrap P5 PnL | > $0 | Worst 5% of 10K resampled paths must still be profitable. |
| H5 | Kelly fraction | Full Kelly <= 0.50, Half Kelly > 0.05 | Full Kelly > 0.50 = unstable edge. Half Kelly < 0.05 = edge too thin to size. |

## Soft Gates (fail = review + conditional deploy)

| # | Gate | Threshold | Monitoring condition if failed |
|---|------|-----------|-------------------------------|
| S1 | Sharpe (daily, annualized) | >= 1.25 | Monitor rolling 20-day Sharpe; halt if < 0.50 for 10 consecutive days. |
| S2 | Sortino (daily, annualized) | >= 1.50 | Monitor downside deviation; halt if daily loss frequency exceeds 2x holdout rate. |
| S3 | Calmar (annualized return / max DD) | >= 0.75 | Monitor DD; halt if live DD exceeds 1.5x holdout max DD. |
| S4 | WR compression headroom | >= 5% before breakeven | Monitor WR weekly; halt if WR drops below breakeven threshold (strategy-specific). |
| S5 | Slippage tolerance | PF >= 1.0 at 2t | Monitor fill quality; halt if avg slippage exceeds 1t over rolling 5-day window. |
| S6 | Max DD (% of allocated capital) | <= 15% | Defined at deployment time. Halt if breached. |

---

## Tests Applied

### Profit Factor (G1)

PF = gross_wins / gross_losses, computed after commission
($4.12 RT per contract per side). Slippage NOT included in raw PF
— slippage tolerance is tested separately in G7.

### Sharpe Ratio (G2)

Daily PnL aggregated by trading day. Annualized by sqrt(252).
Risk-free rate = 0 (futures are margined, no opportunity cost
on full notional).

### Sortino Ratio (G3)

Same as Sharpe but denominator uses downside deviation only
(std of negative daily returns). Better for strategies with
asymmetric return profiles (high WR, occasional large losses).

### Calmar Ratio (G4)

Annualized total PnL / max drawdown (daily equity curve).
Annualization: total_pnl * (252 / trading_days).

### Minimum Cycles (G5)

Raw cycle count from holdout tradelog. No adjustment for
position size or contract count.

### WR Compression (G6)

Simulate PnL at reduced win rates (keeping avg win/loss constant).
Find the WR reduction % where PF crosses below 1.0. Headroom =
that reduction %. Must be >= 5%.

### Slippage Tolerance (G7)

Apply fixed slippage per side per contract to each trade's PnL.
Recompute PF at 2 ticks. Must be >= 1.0.

Note: this is post-hoc slippage (subtracted from existing trades),
not re-simulated with adjusted prices. Conservative approximation.

### Serial Correlation (G8)

Autocorrelation of trade PnL sequence at lags 1-5. Significance
threshold = 2 / sqrt(N). Any lag exceeding threshold = FAIL.

### Bootstrap P5 (G9)

10,000 bootstrap resamples of the trade PnL sequence (with
replacement). 5th percentile of total PnL across all paths
must be positive.

### Max Drawdown (G10)

Dollar max DD from daily equity curve (cumulative daily PnL).
Recorded in bench verdict. Converted to % at deployment time
when account size is known.

### Kelly Fraction (G11)

Full Kelly = WR - (1 - WR) / (avg_win / avg_loss).
Must be <= 0.50 (higher = unstable edge).
Half Kelly must be > 0.05 (lower = edge too thin to size).

---

## Multiple Testing Adjustment

If multiple strategies/variants are tested against the same
holdout period, apply Bonferroni correction: divide significance
thresholds by the number of variants tested. Record n_variants
in the verdict.

For the current pipeline (one variant at a time per holdout
period), no adjustment is needed.

---

## Verdict Rules

- **PASS:** All hard gates met, all soft gates met.
- **CONDITIONAL PASS:** All hard gates met, one or more soft gates
  failed. Deploy with monitoring conditions attached. Each failed
  soft gate adds a specific, measurable halt condition to the
  deployment checklist. Human reviews and approves before deploy.
- **FAIL:** Any hard gate not met. No deploy. Returns to lab.

Verdict is permanent for a given holdout period. A strategy that
fails hard gates cannot re-test the same holdout period with
modified params. A CONDITIONAL PASS can proceed to deploy without
re-testing — the monitoring conditions are the safeguard.

### Verdict File Contents

The verdict JSON must record:
- Each gate: threshold, observed value, PASS/FAIL
- Overall verdict: PASS / CONDITIONAL PASS / FAIL
- For CONDITIONAL PASS: list of monitoring conditions
- n_variants tested (for multiple testing adjustment)
- Holdout period, frozen params hash, date produced
