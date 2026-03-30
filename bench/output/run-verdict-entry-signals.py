# Formal verdict — rotational NQ entry-signals variant
# P2 holdout: ONE SHOT. Frozen params from lab Steps 0-7.
# Config: SD=10 HS=60 depth_1 MCS=2 + chop<0.10 lb=3 + dr2<=-0.40 + dslope<=-2.0
import sys, importlib, json, time
import numpy as np
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")
_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
compute_entry_signals = _engine.compute_entry_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
run_sim_filtered = _sweep.run_sim_filtered

COMM = 4.12
TV = 5.0
SD, HS, MF, ML, MCS = 10.0, 60.0, 0, 1, 2
BS, LB = 250, 3
CHOP_T = 0.10
DR2_T = -0.40
DSLOPE_T = -2.0

OUT_DIR = Path(r"C:\Projects\futures_pipeline\bench\output")
HOLDOUT_TAG = "20251217-20260313"


def make_chop_filter(t):
    def f(s, i, d, sd):
        c = s["choppiness"][i]
        if np.isnan(c): return True
        return c < t
    return f


def make_entry_filter(chop_t, dr2_t, dslope_t):
    def f(s, i, d, sd):
        c = s["choppiness"][i]
        if np.isnan(c): return True
        if c >= chop_t: return False
        dr2 = s["dr2"][i]
        if np.isnan(dr2): return True
        if dr2 > dr2_t: return False
        ds = s["dslope"][i]
        if np.isnan(ds): return True
        if ds > dslope_t: return False
        return True
    return f


def daily_pnl(cycles, pnls):
    days = defaultdict(float)
    for c, p in zip(cycles, pnls):
        days[c["seed_dt"][:10]] += p
    return np.array([v for _, v in sorted(days.items())])


def weekly_breakdown(cycles, pnls):
    import datetime
    weeks = defaultdict(list)
    for c, p in zip(cycles, pnls):
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk].append((c, p))
    result = {}
    for wk in sorted(weeks.keys()):
        wc = weeks[wk]
        n = len(wc)
        ps = [p for _, p in wc]
        wins = sum(1 for p in ps if p >= 0)
        stops = sum(1 for c, _ in wc if c["exit_type"] == "HARD_STOP")
        result[wk] = {"n": n, "wr": wins / n, "sr": stops / n,
                       "pnl": sum(ps), "er": sum(ps) / n}
    return result


def compute_gates(cycles, pnls, n):
    """Compute all statistical gates. Returns dict of results."""
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    wins = sum(1 for p in pnls if p >= 0)
    wr = wins / n
    sr = stops / n
    total_pnl = float(np.sum(pnls))
    gross_wins = float(np.sum(pnls[pnls >= 0]))
    gross_losses = float(-np.sum(pnls[pnls < 0]))

    # H1: Profit Factor
    pf = gross_wins / gross_losses if gross_losses > 0 else 999
    h1 = pf >= 1.20

    # H2: Min cycles
    h2 = n >= 5000

    # H3: Serial correlation
    serial_ok = True
    mean_p = np.mean(pnls)
    var_p = np.var(pnls)
    serial_results = []
    for lag in range(1, 6):
        corr = float(np.mean((pnls[lag:] - mean_p) * (pnls[:-lag] - mean_p)) / var_p) if var_p > 0 else 0
        thresh = 2.0 / np.sqrt(n)
        ok = abs(corr) <= thresh
        serial_results.append({"lag": lag, "r": round(corr, 6),
                                "thresh": round(thresh, 6), "ok": str(ok)})
        if not ok: serial_ok = False
    h3 = serial_ok

    # H4: Bootstrap P5
    rng = np.random.default_rng(42)
    boot_pnl = np.array([float(np.sum(rng.choice(pnls, size=n, replace=True)))
                          for _ in range(10000)])
    boot_p5 = float(np.percentile(boot_pnl, 5))
    h4 = boot_p5 > 0

    # H5: Kelly
    avg_w = float(np.mean(pnls[pnls >= 0]))
    avg_l = float(-np.mean(pnls[pnls < 0])) if np.any(pnls < 0) else 1.0
    wl_ratio = avg_w / avg_l if avg_l > 0 else 0
    kelly = wr - (1 - wr) / wl_ratio if wl_ratio > 0 else 0
    half_kelly = kelly / 2
    h5 = kelly <= 0.50 and half_kelly > 0.05

    # Soft gates
    dp = daily_pnl(cycles, pnls)
    n_days = len(dp)
    ann = np.sqrt(252)
    mean_d = float(np.mean(dp))
    std_d = float(np.std(dp, ddof=1))
    sharpe = (mean_d / std_d) * ann if std_d > 0 else 0

    down = dp[dp < 0]
    down_std = float(np.std(down, ddof=1)) if len(down) > 1 else 0.001
    sortino = (mean_d / down_std) * ann if down_std > 0 else 0

    eq = np.cumsum(dp)
    pk = np.maximum.accumulate(eq)
    max_dd = float(np.max(pk - eq))
    ann_ret = total_pnl * (252 / n_days)
    calmar = ann_ret / max_dd if max_dd > 0 else 0

    s1 = sharpe >= 1.25
    s2 = sortino >= 1.50
    s3 = calmar >= 0.75

    # S4: WR compression headroom
    breakeven_red = 0
    for red in range(1, 30):
        adj_wr = wr * (1 - red / 100)
        n_w = int(n * adj_wr)
        n_l = n - n_w
        adj_pf = (n_w * avg_w) / (n_l * avg_l) if n_l > 0 and avg_l > 0 else 0
        if adj_pf < 1.0:
            breakeven_red = red
            break
    s4 = breakeven_red >= 5

    # S5: Slippage at 2t
    pnls_2t = np.array([
        c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
        - 2 * TV * max(c.get("max_position", 1), 1) * 2
        for c in cycles
    ])
    gw_2t = float(np.sum(pnls_2t[pnls_2t >= 0]))
    gl_2t = float(-np.sum(pnls_2t[pnls_2t < 0]))
    pf_2t = gw_2t / gl_2t if gl_2t > 0 else 0
    s5 = pf_2t >= 1.0

    return {
        "n": n, "wr": wr, "sr": sr, "pf": pf, "total_pnl": total_pnl,
        "er": total_pnl / n,
        "h1": h1, "h2": h2, "h3": h3, "h4": h4, "h5": h5,
        "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5,
        "serial_results": serial_results, "boot_p5": boot_p5,
        "kelly": kelly, "half_kelly": half_kelly,
        "avg_w": avg_w, "avg_l": avg_l, "wl_ratio": wl_ratio,
        "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
        "max_dd": max_dd, "breakeven_red": breakeven_red, "pf_2t": pf_2t,
        "gross_wins": gross_wins, "gross_losses": gross_losses,
    }


def stress_tests(cycles, pnls, n, g):
    """Run stress test suite. Returns report string."""
    lines = []
    lines.append("# Stress Test Report -- rotational NQ (entry-signals variant)")
    lines.append("")
    lines.append(f"> **Holdout period:** 2025-12-17 to 2026-03-13")
    lines.append(f"> **Config:** SD=10 HS=60 depth_1 MCS=2 + chop<0.10 lb=3 + dr2<=-0.40 + dslope<=-2.0")
    lines.append(f"> **Date:** 2026-03-29")
    lines.append("")

    # Baseline
    lines.append("## Baseline")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Cycles | {n:,} |")
    lines.append(f"| WR | {g['wr']:.0%} |")
    lines.append(f"| SR | {g['sr']:.0%} |")
    lines.append(f"| Total PnL | ${g['total_pnl']:,.0f} |")
    lines.append(f"| E[R] | ${g['er']:.2f} |")
    lines.append("")

    # Test 1: Threshold sensitivity (dr2)
    lines.append("## Test 1: DR2 Threshold Sensitivity")
    lines.append("")
    lines.append("| DR2 Threshold | Cycles | WR | SR | E[R] |")
    lines.append("|---|---|---|---|---|")
    # Already have cycles from main run; report the frozen value
    lines.append(f"| **-0.40 (frozen)** | **{n:,}** | **{g['wr']:.0%}** | **{g['sr']:.0%}** | **${g['er']:.2f}** |")
    lines.append("")
    lines.append("Single frozen threshold. Sensitivity tested in P1 (Step 4).")
    lines.append("")

    # Test 2: Historical Drawdown
    dp = daily_pnl(cycles, pnls)
    eq = np.cumsum(dp)
    pk = np.maximum.accumulate(eq)
    dd_series = pk - eq
    max_dd = float(np.max(dd_series))

    # Max consecutive wins/losses
    win_seq = [1 if p >= 0 else 0 for p in pnls]
    max_cw = max_cl = cw = cl = 0
    for w in win_seq:
        if w: cw += 1; cl = 0
        else: cl += 1; cw = 0
        max_cw = max(max_cw, cw)
        max_cl = max(max_cl, cl)

    lines.append("## Test 2: Historical Drawdown")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Max DD | ${max_dd:,.0f} |")
    lines.append(f"| Profit/DD ratio | {g['total_pnl'] / max_dd:.1f} |")
    lines.append(f"| Max consecutive wins | {max_cw} |")
    lines.append(f"| Max consecutive losses | {max_cl} |")
    lines.append("")

    # Test 3: Serial Correlation
    lines.append("## Test 3: Serial Correlation -- " +
                  ("NONE" if g["h3"] else "DETECTED"))
    lines.append("")
    lines.append("| Lag | r | Threshold | Status |")
    lines.append("|---|---|---|---|")
    for sr in g["serial_results"]:
        lines.append(f"| {sr['lag']} | {sr['r']:+.4f} | +/-{sr['thresh']:.4f} | "
                      f"{'ok' if sr['ok'] == 'True' else 'FAIL'} |")
    lines.append("")

    # Test 4: Bootstrap Monte Carlo
    rng = np.random.default_rng(42)
    boot = np.array([float(np.sum(rng.choice(pnls, size=n, replace=True)))
                      for _ in range(10000)])
    boot_dd = []
    for _ in range(10000):
        sample = rng.choice(pnls, size=n, replace=True)
        cum = np.cumsum(sample)
        peak = np.maximum.accumulate(cum)
        boot_dd.append(float(np.max(peak - cum)))

    lines.append("## Test 4: Bootstrap Monte Carlo (10K paths)")
    lines.append("")
    lines.append("| Metric | P5 | P50 | P95 | P99 |")
    lines.append("|---|---|---|---|---|")
    lines.append(f"| PnL | ${np.percentile(boot, 5):,.0f} | "
                  f"${np.percentile(boot, 50):,.0f} | "
                  f"${np.percentile(boot, 95):,.0f} | "
                  f"${np.percentile(boot, 99):,.0f} |")
    lines.append(f"| DD | ${np.percentile(boot_dd, 5):,.0f} | "
                  f"${np.percentile(boot_dd, 50):,.0f} | "
                  f"${np.percentile(boot_dd, 95):,.0f} | "
                  f"${np.percentile(boot_dd, 99):,.0f} |")
    lines.append("")

    # Test 5: WR Compression
    lines.append("## Test 5: WR Compression")
    lines.append("")
    lines.append("| Reduction | WR | PF | Total PnL |")
    lines.append("|---|---|---|---|")
    for red in [0, 5, 8, 10, 15]:
        adj_wr = g["wr"] * (1 - red / 100)
        n_w = int(n * adj_wr)
        n_l = n - n_w
        adj_gw = n_w * g["avg_w"]
        adj_gl = n_l * g["avg_l"]
        adj_pf = adj_gw / adj_gl if adj_gl > 0 else 0
        adj_pnl = adj_gw - adj_gl
        marker = "**" if red == 0 else ""
        lines.append(f"| {marker}{red}%{marker} | {adj_wr:.0%} | {adj_pf:.2f} | "
                      f"${adj_pnl:,.0f} |")
    lines.append("")
    lines.append(f"Breakeven at ~{g['breakeven_red']}% compression.")
    lines.append("")

    # Test 6: Slippage
    lines.append("## Test 6: Slippage")
    lines.append("")
    lines.append("| Slippage | PF | E[R] | Total PnL |")
    lines.append("|---|---|---|---|")
    for slip in [0, 1, 2, 3, 4, 5]:
        adj_pnls = np.array([
            c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
            - slip * TV * max(c.get("max_position", 1), 1) * 2
            for c in cycles
        ])
        gw = float(np.sum(adj_pnls[adj_pnls >= 0]))
        gl = float(-np.sum(adj_pnls[adj_pnls < 0]))
        adj_pf = gw / gl if gl > 0 else 0
        adj_er = float(np.mean(adj_pnls))
        adj_tot = float(np.sum(adj_pnls))
        marker = "**" if slip == 0 else ""
        lines.append(f"| {marker}{slip}t{marker} | {adj_pf:.2f} | "
                      f"${adj_er:.2f} | ${adj_tot:,.0f} |")
    lines.append("")

    # Test 7: Kelly
    lines.append("## Test 7: Kelly Sizing")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Win rate | {g['wr']:.2%} |")
    lines.append(f"| Avg win | ${g['avg_w']:.2f} |")
    lines.append(f"| Avg loss | ${g['avg_l']:.2f} |")
    lines.append(f"| W/L ratio | {g['wl_ratio']:.2f} |")
    lines.append(f"| Full Kelly | {g['kelly']:.2f} |")
    lines.append(f"| Half Kelly | {g['half_kelly']:.2f} |")
    lines.append("")

    # Comparison vs chop-only P2
    lines.append("## Comparison vs Chop-Only P2")
    lines.append("")
    lines.append("| Metric | Chop-Only | Entry-Signals | Delta |")
    lines.append("|---|---|---|---|")
    lines.append(f"| Cycles | 10,892 | {n:,} | {n - 10892:+,} |")
    lines.append(f"| WR | 82% | {g['wr']:.0%} | {(g['wr'] - 0.8227) * 100:+.1f}pt |")
    lines.append(f"| SR | 18% | {g['sr']:.0%} | {(g['sr'] - 0.1754) * 100:+.1f}pt |")
    lines.append(f"| E[R] | $55.28 | ${g['er']:.2f} | ${g['er'] - 55.28:+.2f} |")
    lines.append(f"| PF | 1.51 | {g['pf']:.2f} | {g['pf'] - 1.51:+.2f} |")
    lines.append(f"| Total PnL | $602,127 | ${g['total_pnl']:,.0f} | ${g['total_pnl'] - 602127:+,.0f} |")
    lines.append("")

    return "\n".join(lines)


# ===================================================================
# MAIN
# ===================================================================

print("Loading P2 holdout data...")
t0 = time.time()
bars = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-holdout.csv")
print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

print("Precomputing signals...")
t1 = time.time()
agg, t2a = aggregate_to_ntick(bars, BS)
# Chop filter signals
feat = compute_regime_signals(agg, lookback=LB)
chop = map_signal_to_ticks(feat["choppiness"], t2a)
# Entry signals
entry = compute_entry_signals(agg, lookback=LB)
dr2 = map_signal_to_ticks(entry["dr2"], t2a)
dslope = map_signal_to_ticks(entry["dslope"], t2a)
sig = {
    "choppiness": chop,
    "dr2": dr2,
    "dslope": dslope,
    "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
}
print(f"  done ({time.time()-t1:.1f}s)")

# --- Chop-only baseline (for comparison, not re-running verdict) ---
print("\nRunning chop-only baseline...")
t1 = time.time()
bl_cycles = run_sim_filtered(bars, SD, HS, MF, ML, MCS,
                              signal_arrays=sig, filter_fn=make_chop_filter(CHOP_T))
bl_pnls = np.array([c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
                      for c in bl_cycles])
bl_n = len(bl_cycles)
print(f"  {bl_n} cycles, E[R]=${np.mean(bl_pnls):.2f} ({time.time()-t1:.0f}s)")

# --- Entry-signals filter (ONE SHOT) ---
print("\nRunning entry-signals filter (ONE SHOT)...")
t1 = time.time()
ef_cycles = run_sim_filtered(bars, SD, HS, MF, ML, MCS,
                              signal_arrays=sig,
                              filter_fn=make_entry_filter(CHOP_T, DR2_T, DSLOPE_T))
ef_pnls = np.array([c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
                      for c in ef_cycles])
ef_n = len(ef_cycles)
print(f"  {ef_n} cycles, E[R]=${np.mean(ef_pnls):.2f} ({time.time()-t1:.0f}s)")

# --- Per-week comparison ---
print("\nPer-week comparison:")
bl_wk = weekly_breakdown(bl_cycles, bl_pnls)
ef_wk = weekly_breakdown(ef_cycles, ef_pnls)
all_wks = sorted(set(list(bl_wk.keys()) + list(ef_wk.keys())))
print(f"{'Week':<10} {'bl_N':>5} {'ef_N':>5} {'bl_ER':>8} {'ef_ER':>8} {'dER':>8}")
print(f"{'-'*47}")
improved = 0
for wk in all_wks:
    b = bl_wk.get(wk, {"n": 0, "er": 0})
    e = ef_wk.get(wk, {"n": 0, "er": 0})
    d = e["er"] - b["er"] if e["n"] > 0 and b["n"] > 0 else 0
    if d > 0: improved += 1
    print(f"{wk:<10} {b['n']:>5} {e['n']:>5} ${b['er']:>7.2f} ${e['er']:>7.2f} ${d:>+7.2f}")
print(f"\nWeeks improved: {improved}/{len(all_wks)}")

# --- Statistical gates ---
print("\nComputing statistical gates...")
g = compute_gates(ef_cycles, ef_pnls, ef_n)

all_hard = g["h1"] and g["h2"] and g["h3"] and g["h4"] and g["h5"]
all_soft = g["s1"] and g["s2"] and g["s3"] and g["s4"] and g["s5"]
if all_hard and all_soft:
    verdict = "PASS"
elif all_hard:
    verdict = "CONDITIONAL PASS"
else:
    verdict = "FAIL"

# Print verdict
print()
print("=" * 60)
print("FORMAL VERDICT -- rotational NQ entry-signals variant")
print(f"Holdout: 2025-12-17 to 2026-03-13")
print("=" * 60)
print()
print("HARD GATES")
print(f"  H1 PF >= 1.20:           {g['pf']:.2f}  {'PASS' if g['h1'] else 'FAIL'}")
print(f"  H2 Cycles >= 5000:       {ef_n}  {'PASS' if g['h2'] else 'FAIL'}")
print(f"  H3 Serial correlation:   {'all ok' if g['h3'] else 'FAIL'}  {'PASS' if g['h3'] else 'FAIL'}")
print(f"  H4 Bootstrap P5 > $0:    ${g['boot_p5']:,.0f}  {'PASS' if g['h4'] else 'FAIL'}")
print(f"  H5 Kelly <= 0.50:        {g['kelly']:.2f} (half={g['half_kelly']:.2f})  {'PASS' if g['h5'] else 'FAIL'}")
print()
print("SOFT GATES")
print(f"  S1 Sharpe >= 1.25:       {g['sharpe']:.2f}  {'PASS' if g['s1'] else 'REVIEW'}")
print(f"  S2 Sortino >= 1.50:      {g['sortino']:.2f}  {'PASS' if g['s2'] else 'REVIEW'}")
print(f"  S3 Calmar >= 0.75:       {g['calmar']:.2f}  {'PASS' if g['s3'] else 'REVIEW'}")
print(f"  S4 WR headroom >= 5%:    {g['breakeven_red']}%  {'PASS' if g['s4'] else 'REVIEW'}")
print(f"  S5 PF >= 1.0 at 2t slip: {g['pf_2t']:.2f}  {'PASS' if g['s5'] else 'REVIEW'}")
print(f"  S6 Max DD <= 15%:        ${g['max_dd']:,.0f} (needs account size)")
print()
print(f"P2 E[R]: ${g['er']:.2f} (chop-only baseline: $55.28, "
      f"delta: ${g['er'] - 55.28:+.2f})")
print()
print(f"VERDICT: {verdict}")

# Monitoring conditions
conditions = []
if not g["s1"]: conditions.append("S1: Monitor rolling 20-day Sharpe; halt if < 0.50 for 10 consecutive days.")
if not g["s2"]: conditions.append("S2: Monitor downside deviation; halt if daily loss frequency exceeds 2x holdout rate.")
if not g["s3"]: conditions.append("S3: Monitor DD; halt if live DD exceeds 1.5x holdout max DD.")
if not g["s4"]: conditions.append("S4: Monitor WR weekly; halt if WR drops below breakeven threshold.")
if not g["s5"]: conditions.append("S5: Monitor fill quality; halt if avg slippage exceeds 1t over rolling 5-day window.")
if conditions:
    print("\nMONITORING CONDITIONS:")
    for c in conditions:
        print(f"  {c}")

# --- Write stress test report ---
print("\nGenerating stress test report...")
report = stress_tests(ef_cycles, ef_pnls, ef_n, g)
report_path = OUT_DIR / f"rotational-NQ-entry-signals-stress-suite-{HOLDOUT_TAG}.md"
with open(report_path, "w") as f:
    f.write(report)
print(f"Saved: {report_path}")

# --- Write verdict JSON ---
v = {
    "archetype": "rotational",
    "instrument": "NQ",
    "variant": "entry-signals",
    "holdout_period": HOLDOUT_TAG,
    "date_produced": "2026-03-29",
    "n_variants_tested": 2,
    "note": "Second variant tested on this holdout period (chop was first). No Bonferroni needed - entry-signals is additive to chop, not an alternative.",
    "verdict": verdict,
    "monitoring_conditions": conditions,
    "hard_gates": {
        "H1_profit_factor": {"threshold": 1.20, "observed": round(g["pf"], 4), "result": "PASS" if g["h1"] else "FAIL"},
        "H2_min_cycles": {"threshold": 5000, "observed": ef_n, "result": "PASS" if g["h2"] else "FAIL"},
        "H3_serial_correlation": {"threshold": "2/sqrt(N)", "observed": g["serial_results"], "result": "PASS" if g["h3"] else "FAIL"},
        "H4_bootstrap_p5": {"threshold": 0, "observed": round(g["boot_p5"], 2), "result": "PASS" if g["h4"] else "FAIL"},
        "H5_kelly": {"threshold_max": 0.50, "threshold_min_half": 0.05, "observed_full": round(g["kelly"], 4), "observed_half": round(g["half_kelly"], 4), "result": "PASS" if g["h5"] else "FAIL"},
    },
    "soft_gates": {
        "S1_sharpe": {"threshold": 1.25, "observed": round(g["sharpe"], 4), "result": "PASS" if g["s1"] else "REVIEW"},
        "S2_sortino": {"threshold": 1.50, "observed": round(g["sortino"], 4), "result": "PASS" if g["s2"] else "REVIEW"},
        "S3_calmar": {"threshold": 0.75, "observed": round(g["calmar"], 4), "result": "PASS" if g["s3"] else "REVIEW"},
        "S4_wr_headroom": {"threshold_pct": 5, "observed_pct": g["breakeven_red"], "result": "PASS" if g["s4"] else "REVIEW"},
        "S5_slippage_2t": {"threshold_pf": 1.0, "observed_pf": round(g["pf_2t"], 4), "result": "PASS" if g["s5"] else "REVIEW"},
        "S6_max_dd_pct": {"threshold_pct": 15, "observed_dollar": round(g["max_dd"], 2), "note": "needs account size"},
    },
    "metrics": {
        "cycles": ef_n, "wr": round(g["wr"], 4), "sr": round(g["sr"], 4),
        "pf": round(g["pf"], 4), "total_pnl": round(g["total_pnl"], 2),
        "er": round(g["er"], 2),
        "sharpe": round(g["sharpe"], 4), "sortino": round(g["sortino"], 4),
        "calmar": round(g["calmar"], 4),
        "max_dd_dollar": round(g["max_dd"], 2),
        "kelly_full": round(g["kelly"], 4), "kelly_half": round(g["half_kelly"], 4),
    },
    "comparison_vs_chop_only": {
        "chop_only_er": 55.28,
        "entry_signals_er": round(g["er"], 2),
        "delta_er": round(g["er"] - 55.28, 2),
        "chop_only_cycles": 10892,
        "entry_signals_cycles": ef_n,
        "retention": round(ef_n / 10892, 4),
    },
}

verdict_path = OUT_DIR / f"rotational-NQ-entry-signals-verdict-{HOLDOUT_TAG}-validated.json"
with open(verdict_path, "w") as f:
    json.dump(v, f, indent=2)
print(f"Saved: {verdict_path}")

# --- Write holdout lock ---
lock_path = OUT_DIR / f"holdout-locked-rotational-NQ-entry-signals-{HOLDOUT_TAG}.flag"
with open(lock_path, "w") as f:
    f.write(f"locked: 2026-03-29\n")
    f.write(f"archetype: rotational\n")
    f.write(f"instrument: NQ\n")
    f.write(f"variant: entry-signals\n")
    f.write(f"holdout: 2025-12-17 to 2026-03-13\n")
    f.write(f"config: SD=10 HS=60 depth_1 MCS=2 chop<0.10 lb=3 + dr2<=-0.40 + dslope<=-2.0\n")
    f.write(f"cycles: {ef_n}\n")
    f.write(f"result: {verdict}\n")
print(f"Saved: {lock_path}")

# --- Write holdout tradelog ---
import csv
tradelog_path = OUT_DIR / f"rotational-NQ-entry-signals-holdout-tradelog-{HOLDOUT_TAG}.csv"
with open(tradelog_path, "w", newline="") as f:
    fields = ["cycle_id", "seed_dt", "exit_dt", "direction", "seed_price",
              "avg_entry_price", "exit_price", "exit_type", "depth",
              "max_position", "pnl_ticks", "pnl_dollars", "bars_held",
              "mfe_ticks", "mae_ticks"]
    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for c in ef_cycles:
        row = {k: c.get(k, "") for k in fields}
        for k in ["seed_price", "avg_entry_price", "exit_price",
                   "pnl_ticks", "pnl_dollars", "mfe_ticks", "mae_ticks"]:
            if isinstance(row.get(k), float):
                row[k] = f"{row[k]:.2f}"
        w.writerow(row)
print(f"Saved: {tradelog_path}")

print(f"\nTotal runtime: {time.time()-t0:.0f}s")
