# Range Fade Rotation — Band & Ratio Quick Reference

## Main Band Formulas

All bands anchor off the **midline (mean)** and scale by **stdDev**.

| Band | Formula |
|------|---------|
| Inner Top | `mean + innerMult * stdDev` |
| Inner Bot | `mean - innerMult * stdDev` |
| Outer Top | `mean + outerMult * stdDev` |
| Outer Bot | `mean - outerMult * stdDev` |

## Target & Stop Formulas

| Measure | Formula |
|---------|---------|
| Target offset | `innerTop - innerBot` = `2 * innerMult * stdDev` |
| Stop offset | `innerBot - outerBot` = `(outerMult - innerMult) * stdDev` |
| Target:Stop ratio | `2 * innerMult / (outerMult - innerMult)` |

## Ratio Examples

| Inner Mult | Outer Mult | Target (stdDev) | Stop (stdDev) | Ratio |
|------------|------------|-----------------|---------------|-------|
| 1.00 | 1.50 | 2.00 | 0.50 | 4:1 |
| 1.00 | 2.00 | 2.00 | 1.00 | 2:1 |
| 1.25 | 1.50 | 2.50 | 0.25 | 10:1 |
| 1.25 | 2.00 | 2.50 | 0.75 | 3.3:1 |
| 0.75 | 1.50 | 1.50 | 0.75 | 2:1 |
| 0.75 | 2.00 | 1.50 | 1.25 | 1.2:1 |

## Sub-Band Formulas (v3)

Sub-bands replicate the main band structure within each inner-to-outer zone.

| Measure | Formula |
|---------|---------|
| Sub-midline (top) | `(innerTop + outerTop) / 2` |
| Half-width (top) | `(outerTop - innerTop) / 2` |
| Sub-inner upper | `subMid + subInnerMult * halfWidth` |
| Sub-inner lower | `subMid - subInnerMult * halfWidth` |
| Sub-outer upper | `subMid + subOuterMult * halfWidth` |
| Sub-outer lower | `subMid - subOuterMult * halfWidth` |

At `subInnerMult = 1.0`: sub-inner lines match inner/outer bands.
At `subOuterMult = 1.0`: sub-outer lines match inner/outer bands.
