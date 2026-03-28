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
