# Formal verdict against statistical gates
import sys, importlib, json
import numpy as np
from collections import defaultdict

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")
_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
run_sim_filtered = _sweep.run_sim_filtered
COMM = 4.12
TV = 5.0

SD, HS, MF, ML, MCS = 10.0, 60.0, 0, 1, 2
BS, LB = 250, 3


def make_filter(t):
    def f(s, i, d, sd):
        c = s["choppiness"][i]
        if np.isnan(c):
            return True
        return c < t
    return f


def daily_pnl(cycles, pnls):
    days = defaultdict(float)
    for c, p in zip(cycles, pnls):
        days[c["seed_dt"][:10]] += p
    return np.array([v for _, v in sorted(days.items())])


print("Loading P2...")
bars = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-holdout.csv")
agg, t2a = aggregate_to_ntick(bars, BS)
feat = compute_regime_signals(agg, lookback=LB)
chop = map_signal_to_ticks(feat["choppiness"], t2a)
sig = {"choppiness": chop, "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0}}

print("Running filtered sim...")
cycles = run_sim_filtered(bars, SD, HS, MF, ML, MCS,
                          signal_arrays=sig, filter_fn=make_filter(0.10))
n = len(cycles)
pnls = np.array([c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
                  for c in cycles])
stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
wins = sum(1 for p in pnls if p >= 0)
wr = wins / n
sr = stops / n
total_pnl = float(np.sum(pnls))
gross_wins = float(np.sum(pnls[pnls >= 0]))
gross_losses = float(-np.sum(pnls[pnls < 0]))

# === HARD GATES ===

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
    serial_results.append({"lag": lag, "r": round(corr, 6), "thresh": round(thresh, 6), "ok": str(ok)})
    if not ok:
        serial_ok = False
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

# === SOFT GATES ===

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

# S5: Slippage at 2t (post-hoc)
pnls_2t = np.array([
    c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
    - 2 * TV * max(c.get("max_position", 1), 1) * 2
    for c in cycles
])
gw_2t = float(np.sum(pnls_2t[pnls_2t >= 0]))
gl_2t = float(-np.sum(pnls_2t[pnls_2t < 0]))
pf_2t = gw_2t / gl_2t if gl_2t > 0 else 0
s5 = pf_2t >= 1.0

# === VERDICT ===

all_hard = h1 and h2 and h3 and h4 and h5
all_soft = s1 and s2 and s3 and s4 and s5
if all_hard and all_soft:
    verdict = "PASS"
elif all_hard:
    verdict = "CONDITIONAL PASS"
else:
    verdict = "FAIL"

# Print
print()
print("=" * 60)
print("FORMAL VERDICT -- rotational NQ chop variant")
print("Holdout: 2025-12-17 to 2026-03-13")
print("=" * 60)
print()
print("HARD GATES")
print(f"  H1 PF >= 1.20:           {pf:.2f}  {'PASS' if h1 else 'FAIL'}")
print(f"  H2 Cycles >= 5000:       {n}  {'PASS' if h2 else 'FAIL'}")
print(f"  H3 Serial correlation:   {'all ok' if h3 else 'FAIL'}  {'PASS' if h3 else 'FAIL'}")
print(f"  H4 Bootstrap P5 > $0:    ${boot_p5:,.0f}  {'PASS' if h4 else 'FAIL'}")
print(f"  H5 Kelly <= 0.50:        {kelly:.2f} (half={half_kelly:.2f})  {'PASS' if h5 else 'FAIL'}")
print()
print("SOFT GATES")
print(f"  S1 Sharpe >= 1.25:       {sharpe:.2f}  {'PASS' if s1 else 'REVIEW'}")
print(f"  S2 Sortino >= 1.50:      {sortino:.2f}  {'PASS' if s2 else 'REVIEW'}")
print(f"  S3 Calmar >= 0.75:       {calmar:.2f}  {'PASS' if s3 else 'REVIEW'}")
print(f"  S4 WR headroom >= 5%:    {breakeven_red}%  {'PASS' if s4 else 'REVIEW'}")
print(f"  S5 PF >= 1.0 at 2t slip: {pf_2t:.2f}  {'PASS' if s5 else 'REVIEW'}")
print(f"  S6 Max DD <= 15%:        ${max_dd:,.0f} (needs account size)")
print()
print(f"VERDICT: {verdict}")

# Monitoring conditions for any soft gate failures
conditions = []
if not s1:
    conditions.append("S1: Monitor rolling 20-day Sharpe; halt if < 0.50 for 10 consecutive days.")
if not s2:
    conditions.append("S2: Monitor downside deviation; halt if daily loss frequency exceeds 2x holdout rate.")
if not s3:
    conditions.append("S3: Monitor DD; halt if live DD exceeds 1.5x holdout max DD.")
if not s4:
    conditions.append("S4: Monitor WR weekly; halt if WR drops below 76%.")
if not s5:
    conditions.append("S5: Monitor fill quality; halt if avg slippage exceeds 1t over rolling 5-day window.")

if conditions:
    print()
    print("MONITORING CONDITIONS:")
    for c in conditions:
        print(f"  {c}")

# Write verdict JSON
v = {
    "archetype": "rotational",
    "instrument": "NQ",
    "variant": "chop",
    "holdout_period": "20251217-20260313",
    "date_produced": "2026-03-29",
    "n_variants_tested": 1,
    "verdict": verdict,
    "monitoring_conditions": conditions,
    "hard_gates": {
        "H1_profit_factor": {"threshold": 1.20, "observed": round(pf, 4), "result": "PASS" if h1 else "FAIL"},
        "H2_min_cycles": {"threshold": 5000, "observed": n, "result": "PASS" if h2 else "FAIL"},
        "H3_serial_correlation": {"threshold": "2/sqrt(N)", "observed": serial_results, "result": "PASS" if h3 else "FAIL"},
        "H4_bootstrap_p5": {"threshold": 0, "observed": round(boot_p5, 2), "result": "PASS" if h4 else "FAIL"},
        "H5_kelly": {"threshold_max": 0.50, "threshold_min_half": 0.05, "observed_full": round(kelly, 4), "observed_half": round(half_kelly, 4), "result": "PASS" if h5 else "FAIL"},
    },
    "soft_gates": {
        "S1_sharpe": {"threshold": 1.25, "observed": round(sharpe, 4), "result": "PASS" if s1 else "REVIEW"},
        "S2_sortino": {"threshold": 1.50, "observed": round(sortino, 4), "result": "PASS" if s2 else "REVIEW"},
        "S3_calmar": {"threshold": 0.75, "observed": round(calmar, 4), "result": "PASS" if s3 else "REVIEW"},
        "S4_wr_headroom": {"threshold_pct": 5, "observed_pct": breakeven_red, "result": "PASS" if s4 else "REVIEW"},
        "S5_slippage_2t": {"threshold_pf": 1.0, "observed_pf": round(pf_2t, 4), "result": "PASS" if s5 else "REVIEW"},
        "S6_max_dd_pct": {"threshold_pct": 15, "observed_dollar": round(max_dd, 2), "note": "needs account size"},
    },
    "metrics": {
        "cycles": n, "wr": round(wr, 4), "sr": round(sr, 4),
        "pf": round(pf, 4), "total_pnl": round(total_pnl, 2), "er": round(total_pnl / n, 2),
        "sharpe": round(sharpe, 4), "sortino": round(sortino, 4), "calmar": round(calmar, 4),
        "max_dd_dollar": round(max_dd, 2), "kelly_full": round(kelly, 4), "kelly_half": round(half_kelly, 4),
    },
}

out_path = r"C:\Projects\futures_pipeline\bench\output\rotational-NQ-verdict-20251217-20260313-validated.json"
with open(out_path, "w") as f:
    json.dump(v, f, indent=2)
print(f"\nVerdict written to {out_path}")
