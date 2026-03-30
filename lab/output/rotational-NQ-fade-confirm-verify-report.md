# Verify Report: Fade Confirmation Filter

> **Archetype:** rotational
> **Instrument:** NQ
> **Variant:** fade-confirmation
> **Status:** frozen
> **Date:** 2026-03-30

---

## Configuration

**Full filter stack (applied in order):**
1. Choppiness < 0.10 at lb=3 on 250-tick bars
2. dR2 <= -0.40 at lb=3
3. dSlope <= -2.0 at lb=3
4. **fade_confirm < 0.40** (NEW — this study)

**fade_confirm computation:**
- LONG: `(entry_price - prev_bar_low) / (prev_bar_high - prev_bar_low)`
- SHORT: `(prev_bar_high - entry_price) / (prev_bar_high - prev_bar_low)`
- prev_bar = last completed 250-tick aggregated bar before the entry bar
- Guard: zero-range bar → 0.5 (neutral); NaN → allow entry

---

## P1 Results

| | Cycles | WR | SR | E[R] | Total PnL |
|---|---|---|---|---|---|
| Track A baseline | 6,624 | 83% | 17% | $63.22 | $418,785 |
| + fade_confirm < 0.40 | 6,496 | 85% | 15% | $78.16 | $507,737 |

**Improvement:** +$14.94 E[R] (+23.6%), 98% retention, **12/12 weeks improved**.

---

## Per-week breakdown

| Week | BL E[R] | Filt E[R] | dER | Ret |
|------|---------|-----------|-----|-----|
| W39 | $53.81 | $66.66 | +$12.85 | 99% |
| W40 | $37.49 | $42.99 | +$5.49 | 101% |
| W41 | $55.79 | $77.37 | +$21.58 | 99% |
| W42 | $63.30 | $74.14 | +$10.84 | 101% |
| W43 | $50.57 | $60.18 | +$9.61 | 98% |
| W44 | $69.84 | $87.98 | +$18.14 | 96% |
| W45 | $61.57 | $74.93 | +$13.36 | 99% |
| W46 | $60.67 | $71.01 | +$10.34 | 99% |
| W47 | $76.56 | $92.60 | +$16.04 | 95% |
| W48 | $75.28 | $92.68 | +$17.40 | 97% |
| W49 | $54.22 | $88.89 | +$34.67 | 100% |
| W50 | $61.97 | $71.70 | +$9.73 | 98% |

---

## Sanity check

Random filter at 98% pass rate (10 seeds):
- Best random E[R]: $5.56
- fade_confirm E[R]: $78.16
- Margin: $72.60

**PASS** — signal is real, not an artifact of filtering.

---

## Feature selection summary

6 features tested at entry (all using completed bars before entry):
1. **fade_confirm** (close position in prev bar) — 23.4pt SR spread — **WINNER**
2. fade_speed (directional speed of prev bar) — 19.7pt — redundant when combined
3. flow_confirm (volume shift) — 15.2pt — rho=0.72 with fade_speed, redundant
4. direction_bars (consecutive bar direction) — 11.9pt — rho=0.67 with fade_speed, weakest
5. avg_range_decay (multi-bar range ratio) — 5.1pt — moderate, non-monotonic
6. range_decay_1 (single bar range ratio) — 3.0pt — marginal

Key finding: all hypotheses inverted. Fades work best when the pullback is at maximum momentum (low fade_confirm), not when exhausting.

---

## Handoff to bench

Frozen params: `lab/output/rotational-NQ-fade-confirm-params-frozen.json`

Bench should:
1. Copy frozen params to `bench/output/`
2. Run P2 holdout (ONE SHOT)
3. Run stress tests (threshold sensitivity, slippage, Monte Carlo, WR compression)
4. Issue verdict
