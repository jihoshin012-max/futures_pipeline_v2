# Lessons

## 2026-03-29 — Lab/bench boundary bypass

**Failure mode:** The scale detection study prompt defined a test sequence (Steps 1-9 + sanity + stress + P2 holdout) that ran entirely in lab. P2 holdout and stress tests should have been routed to bench per the pipeline. The prompt treated P2 as "just another step" rather than a workspace handoff.

**Detection signal:** Bench output directory was empty after P2 was declared PASS. No lock flag, no tradelog in bench format, no formal verdict.

**Prevention rule:** Lab study prompts must stop at "frozen params + verify report ready." The prompt's test sequence must NOT include holdout validation or stress testing — those belong to bench. Add an explicit "Handoff to bench" section at the end of every lab study prompt, referencing `bench/CONTEXT.md`.

**Applied fix:** Retroactively moved artifacts to bench/output/ with proper naming. Added lock flag. Will update the scale detection prompt and chop momentum prompt to enforce the boundary.

---

## 2026-03-29 — False claim about simulator behavior

**Failure mode:** Stated that the strategy "wouldn't capture all 10K+ entries" because "the strategy requires being flat to seed" and implied signals were missed while in a trade. This was fabricated — the simulator is sequential: each cycle completes before the next begins, and there are no missed entries. The claim was made without reading the simulator code first.

**Detection signal:** User questioned the statement.

**Prevention rule:** Never describe how code works without reading it first. If unsure, say "I'm not sure" and check. The cost of a wrong confident answer far exceeds the cost of admitting uncertainty. This is especially critical for strategy mechanics where a false claim can lead to redesign decisions based on a non-existent problem.

---

## 2026-03-29 — Python aggregation session boundary mismatch

**Failure mode:** Python's `_aggregate_bars()` reset tick counting on date change (midnight). SC's 250-tick chart counts continuously within the session (18:00–17:00). This caused all bar boundaries after midnight to diverge, producing different choppiness values.

**Detection signal:** Replay test showed 0/7 cycles matching Python despite correct formula. Bar count comparison (SC 1776 vs Python 1777) revealed the offset.

**Prevention rule:** When aggregating tick data to match a charting platform's bars, verify bar boundaries against the platform's exported bars before running any validation. Never assume date-change equals session boundary for futures.

---
