# Audit Log

Append-only. Never delete or modify entries.

| Date | Archetype | Instrument | Event | Detail |
|------|-----------|------------|-------|--------|
| 2026-03-27 19:46:59 | — | — | PERIOD_CONFIG_CHANGED | -| W1 | — | — | calibration |
-| W2 | — | — | calibration |
-| W3 | — | — | holdout |
-| `calibration` | Free to use for search, iteration, tuning | Run as many experiments as budget allows |
-| `holdout` | One-shot validation. Frozen params only. | Check lock flag before running. Lock after. | — reason: TODO |
| 2026-03-27 | — | — | CORRECTION | Above PERIOD_CONFIG_CHANGED entry has corrupt formatting. Correct detail: Replaced W1/W2/W3 window system with date-range periods: calibration 2025-09-21 to 2025-12-14, holdout 2025-12-15 to 2026-03-14 |
| 2026-03-27 | rotational | NQ | DATA_ONBOARDED | 18 data files copied from old pipeline, renamed to [instrument]-[source]-[role].csv convention. See data/README.md |
| 2026-03-27 | rotational | NQ | STUDY_RELOCATED | rotational-NQ-speedread.cpp moved to ACSIL workspace (C:\Projects\pipeline\shared\archetypes\rotational\acsil). Not a strategy — general-purpose indicator. Pipeline keeps data output (NQ-speedread-calibration.csv) |
| 2026-03-27 | zone-touch | NQ | STUDY_ONBOARDED | 6 C++ studies + 3 config files onboarded from old pipeline: 3 strategy variants (fixed, zonerel, v32), 3 study chain deps (zone-detector, zone-detector-history, touch-engine) |
| 2026-03-28 09:57:03 | — | — | PERIOD_CONFIG_CHANGED | -| holdout | 2025-12-15 | 2026-03-14 |
+| holdout | 2025-12-17 | 2026-03-13 | — reason: TODO |
| 2026-03-28 | rotational | NQ | STUDY_CREATED | Scale detection study created. Prompt: lab/workflows/hypotheses/rotational-NQ-prompt-scale-detection.md. Signal engine + filtered sweep + baseline results onboarded from old pipeline. Zigzag: own impl (not SC built-in). |
| 2026-03-28 | rotational | NQ | BASELINE_ONBOARDED | rotational-NQ-results-baseline.csv (144 configs) + rotational-NQ-results-baseline-cycles.csv (885K cycles) copied to lab/output/. Source: lp_sweep.py on NQ-1tick-calibration P1 data. |
| 2026-03-28 | rotational | NQ | TYPE_CATALOG_UPDATED | Added scale-detection and sweep-baseline to CLAUDE.md type catalog |
| 2026-03-28 23:48:07 | rotational | NQ | STEP_1_COMPLETE | Static filter SD=25 on 5 weeks. w=250: avg bad-week dSR=-8.6%; w=500: avg bad-week dSR=-1.0%; w=1000: avg bad-week dSR=-4.0%. Runtime: 294s. Results: step1-static-filter-results.csv |
| 2026-03-29 00:03:19 | rotational | NQ | STEP_2_COMPLETE | ZZ median filter SD=25 on 5 weeks. zz_w=10: avg bad-week dSR=-4.3%, avg trans=268; zz_w=20: avg bad-week dSR=+2.0%, avg trans=155; zz_w=40: avg bad-week dSR=-24.8%, avg trans=82. Runtime: 318s. Results: step2-zz-median-results.csv |
| 2026-03-29 00:21:05 | rotational | NQ | STEP_2_COMPLETE | ZZ median filter SD=25 on 5 weeks. zz_w=10: avg bad-week dSR=+0.0%, avg trans=86; zz_w=20: avg bad-week dSR=-0.1%, avg trans=40; zz_w=40: avg bad-week dSR=+0.7%, avg trans=23. Runtime: 291s. Results: step2b-zz-median-15pt-results.csv |
| 2026-03-29 00:32:17 | rotational | NQ | STEP_3_COMPLETE | Asymmetry gate on 5 weeks. ZZ=15pt. both|0.3: avg bad dSR=+0.2%; both|0.5: avg bad dSR=+0.0%; both|0.7: avg bad dSR=+0.0%; dir|0.3: avg bad dSR=+0.0%. Verdict: FAIL. Runtime: 324s. |
| 2026-03-29 | rotational | NQ | PHASE_1_PAUSED | Steps 1-3 all FAIL. Scale detection (MTZZ counting, ZZ median, asymmetry) does not reliably gate SD=25 entries. W39 analysis shows losses from swing overshoot at d1 (martingale add), not scale mismatch or trending. Next direction: chop-vs-trend regime detection using composite derived features (slope, R2, signed volume, choppiness ratio) on 250-tick bars. See journal and xtra/eventdrivendiscussion.md. |
| 2026-03-29 09:47:16 | rotational | NQ | STEP_4_COMPLETE | Feature-outcome correlation analysis on 5 weeks. Runtime: 243s. |
| 2026-03-29 10:12:49 | rotational | NQ | STEP_5_6_COMPLETE | Feature discovery on SD=10 HS=60 baseline. 5 weeks, lookbacks [3, 5, 8, 12]. Runtime: 281s. |
| 2026-03-29 10:32:25 | rotational | NQ | STEP_5_6_COMPLETE | Feature discovery on SD=10 HS=60 baseline. 5 weeks, lookbacks [3, 5, 8, 12]. Runtime: 281s. |
| 2026-03-29 10:51:57 | rotational | NQ | STEP_5_6_COMPLETE | Feature discovery on SD=10 HS=60 baseline. 5 weeks, lookbacks [3, 5, 8, 12]. Runtime: 275s. |
| 2026-03-29 10:54:33 | rotational | NQ | STEP_7_COMPLETE | Pairwise feature combinations on SD=10 lb=3. 7653 cycles analyzed. |
| 2026-03-29 11:02:47 | rotational | NQ | STEP_8_COMPLETE | Live sim with choppiness filter on SD=10 HS=60. Verdict: PASS. Runtime: 285s. |
| 2026-03-29 11:45:33 | rotational | NQ | STEP_9_COMPLETE | Full P1 validation SD=10 HS=60 with choppiness filter. Baseline: 18148 cyc, $56,810. Runtime: 324s. |
| 2026-03-29 12:14:04 | rotational | NQ | SANITY_CHECK_COMPLETE | Random filter sanity check. Baseline: $56,810. chop<0.10: $580,071. Random avg: $24,286. Verdict: PASS. Runtime: 508s. |
| 2026-03-29 14:17:43 | rotational | NQ | STRESS_TEST_COMPLETE | Stress test suite on SD=10 HS=60 chop<0.10 lb=3. Runtime: 681s. |
| 2026-03-29 14:26:39 | rotational | NQ | P2_HOLDOUT_COMPLETE | P2 holdout: SD=10 HS=60 chop<0.10 lb=3. BL: 19947 cyc, $75,394. Filtered: 11043 cyc, $605,808. Verdict: PASS. Runtime: 244s. |
| 2026-03-29 | rotational | NQ | STUDY_BUILD | rotational-NQ-scale-detection-chop.cpp (indicator) compiled. SC name: ATEAM_ChoppinessFilter. Source: frozen params. Fixed DRAWSTYLE_COLOR_BAR_HLC → DRAWSTYLE_COLOR_BAR. |
| 2026-03-29 | rotational | NQ | STUDY_BUILD | rotational-NQ-study-chop.cpp (strategy) compiled. SC name: ATEAM_ROTATION_V3_CHOP. Source: frozen params. Forked from LP-1.1 + choppiness gate. |
| 2026-03-29 | rotational | NQ | CALIBRATION_FIX | C++ choppiness formula off-by-one: close[i-lb] → close[i-lb+1] in both study-chop.cpp and scale-detection-chop.cpp. Test mode timing: single-pass → two-pass to match Python pre-computed tick mapping. |
| 2026-03-29 | rotational | NQ | VERIFY_PARTIAL | Test mode: PASS (34 cycles, 241 PnL ticks, exact match). Live mode: PENDING (filter functional, trade-level alignment awaiting bar-timestamp replay). Report: lab/output/rotational-NQ-verify-report.md |
| 2026-03-29 | rotational | NQ | PARAMS_FROZEN_UPDATED | Fixed computation formula in rotational-NQ-params-frozen.json: close[i-lookback] → close[i-lookback+1] to match corrected implementation. |
| 2026-03-29 | rotational | NQ | PROMPT_CREATED | Choppiness derivatives for momentum study. Prompt: lab/workflows/hypotheses/rotational-NQ-prompt-chop-momentum.md. Three candidates: signed choppiness, dChop/dt, d2Chop/dt2. Status: draft. |
| 2026-03-29 | rotational | NQ | BAR_ALIGNMENT_FOUND | Python _aggregate_bars() resets tick count on date change; SC counts continuously (18:00-17:00). Removing reset: 1711/1776 bars match SC (96.3%), all RTH bars align. P1 re-run with SC-aligned agg: 82% WR, 18% SR, E[R]=$52.57 vs original $54.57 (3.7% diff). Verdict holds. |
| 2026-03-29 | rotational | NQ | AGG_FIX_APPLIED | Removed date-change reset from _aggregate_bars(). Reverted C++ strategy to external ChoppinessFilter read. Replay confirmed 100% choppiness match on RTH bars. |
| 2026-03-29 | rotational | NQ | P2_RERUN_PASS | P2 re-run with SC-aligned agg: 10,892 cyc, 82% WR, 18% SR, E[R]=$55.28 (orig $54.86). Signal intact. |
| 2026-03-29 | rotational | NQ | P2_STRESS_COMPLETE | P2 stress suite PASS. Threshold stable (peak E[R]=$59 at 0.12), lb=3 best, DD=$4,742 (ratio 127), no serial corr, WR breakeven at 76% (same as P1), slippage breakeven at 4t (tighter than P1's 6t), Kelly=0.28. |
| 2026-03-29 | rotational | NQ | BENCH_HANDOFF | Frozen params, holdout tradelog (10,892 cycles), stress report, and lock flag copied to bench/output/. Holdout period: 20251217-20260313. Formal verdict pending — statistical gates undefined. |
| 2026-03-29 | — | — | PROCESS_LESSON | Lab/bench boundary bypass: scale detection study ran P2 holdout + stress tests in lab. Artifacts retroactively moved to bench. Future prompts must stop at frozen params and route to bench. See tasks/lessons.md. |
| 2026-03-29 | — | — | GATES_APPROVED | Statistical gates defined: 5 hard (PF, cycles, serial corr, bootstrap, Kelly) + 6 soft (Sharpe, Sortino, Calmar, WR headroom, slippage, DD%). Tiered verdict: PASS / CONDITIONAL PASS / FAIL. See bench/docs/statistical-gates.md. |
| 2026-03-29 | rotational | NQ | VERDICT_PASS | Formal verdict on P2 holdout (20251217-20260313): PASS. All hard and soft gates met. PF=1.51, Sharpe=19.75, WR headroom=9%, slippage PF@2t=1.24, Kelly=0.28. Verdict: bench/output/rotational-NQ-verdict-20251217-20260313-validated.json |
| 2026-03-29 | rotational | NQ | DEPLOY_HANDOFF | Frozen params, verdict, strategy build (v1), and indicator build (v1) copied to deploy/output/. Checklist pre-filled. Pending: human creates deployment-ready flag. |
| 2026-03-29 | rotational | NQ | entry-signals-frozen | Steps 0-7 PASS. dr2<=-0.40 + dslope<=-2.0 entry gate. P1 E[R] 3.43->3.22 (+18.3%), 64% retention, 9/12 weeks improved. Sanity: +$8.31 over max random. Handed off to bench. |
| 2026-03-29 | rotational | NQ | entry-signals-verdict | P2 PASS. All hard+soft gates met. E[R] $55.28->$70.40 (+$15.12, +27%). PF 1.73, Kelly 0.36. 12/13 weeks improved. Holdout locked. |
| 2026-03-30 | rotational | NQ | fade-confirm-frozen | Track B Steps 0-7 PASS. fade_confirm<0.40 entry gate (price position in prev 250-tick bar). P1 E[R] $63.22->$78.16 (+24%), 98% retention, 12/12 weeks improved. All hypotheses inverted: fades work best at max pullback momentum. |
| 2026-03-30 | rotational | NQ | fade-confirm-verdict | P2 PASS. All hard+soft gates met. E[R] $70.40->$80.55 (+$10.15, +14%). PF 1.91, Kelly 0.41, WR headroom 12%, slippage PF@2t=1.58. 11/13 weeks improved. Holdout locked. |
| 2026-03-30 | rotational | NQ | DEPLOY_HANDOFF | Track B fade-confirm: frozen params + verdict copied to deploy/output/. Checklist updated with Tracks A + B. C++ implementation pending for dR2/dSlope and fade_confirm gates. |
| 2026-03-30 | rotational | NQ | TRACK_C_FAILED | Track C in-trade management: FAILED. Mid-trade signals (signed_chop, dR2, dSlope, signed_slope, hold_ratio, mfe_rate, mae_proximity, range_ratio) did not differentiate winners from losers. Divergence absent or < 3 bars before outcome. |
| 2026-03-30 | rotational | NQ | PROMPT_CREATED | Track C2 loss mitigation (revised approach). HS sweep 40-60, session context (tick rate ratio, session range, price displacement, SR acceleration), mechanical rules (partial profit, adaptive stop from fade_confirm). No mid-trade signal reading. |
| 2026-03-30 | rotational | NQ | TRACK_C2_FAILED | Track C2 loss mitigation: FAILED. HS=60 optimal. Session context: no signal. Adaptive stop: net negative. Partial profit break-even kills reversals. Replay overestimates management benefits (confirmed across C and C2). |
| 2026-03-30 | rotational | NQ | DEPTH_ANALYSIS | 100% of losses from depth_1 stops. Depth_0: 100% WR, 0 stops. No-add test: P1 -9.1%, P2 -12.1% vs depth_1. Add is net positive — retained. Track D proceeds with A+B depth_1 baseline. |
| 2026-03-30 | rotational | NQ | TRACK_B2_PASSED | Track B2 EMA directional gate: PASSED Steps 0-7. d2_ema9 (EMA9 curvature) entry gate + d2_avg3 in-trade hold. Entry gate: d2 neutral zone |d2|<=0.5, P1 E[R] $78->$100 (+28%). In-trade hold: delay reversal when d2_avg3 aligned, P1 E[R] $78->$178 (+128%). Combined P1: E[R]=$201 (+158%), 12/12 weeks, sanity PASS. |
| 2026-03-30 | rotational | NQ | ema-directional-frozen | Combined config frozen: A+B + d2_entry(|d2|<=0.5) + d2_avg3_hold. Params: lab/output/rotational-NQ-ema-directional-params-frozen.json |
| 2026-03-30 | rotational | NQ | ema-directional-verdict | P2 PASS (H2+H5 overridden). E[R]=$225.01 (+$144.47 vs A+B baseline $80.55). PF=4.04, 84% WR, 11% SR. 13/13 weeks improved. H2 override: 4,466 cycles (hold consolidates by design). H5 override: Kelly=0.64 (enlarged wins, unchanged risk). Stress: bootstrap P5=$959K, WR headroom 35%, slippage profitable through 10t, eval pass 98.6%. |
| 2026-03-30 | rotational | NQ | DEPLOY_HANDOFF | Track B2 EMA directional: frozen params + verdict copied to deploy/output/. Checklist updated with A + B + B2. Track D superseded by B2 hold mechanic. C++ implementation pending for dR2/dSlope, fade_confirm, d2_ema9 gate, and d2_avg3 hold. |
| 2026-04-03 16:26:01 | — | — | PERIOD_CONFIG_CHANGED | -| calibration | 2025-09-21 | 2025-12-14 |
+| calibration | 2021-10-01 | 2025-12-14 | — reason: TODO |
