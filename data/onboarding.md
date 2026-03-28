# Data Onboarding

Procedure for adding data to the pipeline. Two scenarios:
holdout roll (when new data extends the timeline) and new
data source (new instrument, bar type, or archetype-specific data).
Either can happen at any time.

**Read `data/README.md` first** — it has the naming convention,
source catalog, and current inventory.

---

## Scenario 1: Holdout Roll

New data extends the timeline. The old holdout becomes
calibration, new data becomes holdout.

### Checklist

1. **Confirm the new data is exported and ready.**
   User provides the source files and the new holdout date range.

2. **For each source that has both calibration and holdout files:**
   - Append old holdout into calibration (skip header row):
     `tail -n +2 [inst]-[source]-holdout.csv >> [inst]-[source]-calibration.csv`
   - Replace holdout file with new quarter's export:
     `cp /path/to/new/export.csv [inst]-[source]-holdout.csv`

3. **For sources with only a calibration file** (e.g., speedread, 1day):
   - Ask user whether new data exists for this source.
   - If yes, append or replace as appropriate.

4. **Update `_config/period-config.md`:**
   - Change holdout start/end to the new quarter
   - Extend calibration end to the old holdout end date
   - Calibration start stays the same

5. **Update `data/README.md`:**
   - Update file sizes in the inventory table
   - Update the period mapping section with new dates

6. **Log to `audit/audit_log.md`:**
   - Event: `HOLDOUT_ROLLED`
   - Detail: old holdout range → calibration, new holdout range

7. **Verify:**
   - Row count of calibration file ≈ old calibration + old holdout
   - Holdout file date range matches new period-config
   - No duplicate headers in concatenated calibration file

---

## Scenario 2: New Data Source

Adding a data type that doesn't exist yet (new instrument,
new bar resolution, new archetype-specific data).

### Checklist

1. **Confirm the source slug.**
   Check `data/README.md` source catalog. If the source type
   doesn't exist, pick a slug and add it to the catalog.

2. **Name the file:**
   `[instrument]-[source]-[role].csv`
   - Role = `calibration` if date range falls within calibration period
   - Role = `holdout` if date range falls within holdout period
   - No role suffix if the data isn't period-bound (e.g., regime-labels)

3. **Copy into `data/`.**

4. **Update `data/README.md`:**
   - Add source to catalog table (with "Consumed By" column)
   - Add file to inventory table (with size and role)

5. **If new instrument:** register in `_config/instruments.md`.

6. **Log to `audit/audit_log.md`:**
   - Event: `DATA_ONBOARDED`
   - Detail: what was added, source, date range

7. **Verify:**
   - File follows naming convention
   - File is gitignored (check `git status data/`)
   - Date range falls within the correct role per period-config

---

## What NOT to do

- Don't create sub-folders in `data/`. All files are flat.
- Don't use the pipeline naming convention (`[arch]-[inst]-[type]-[status]`)
  for data files. Data has its own convention.
- Don't commit CSV files to git. They're gitignored.
- Don't onboard deprecated or superseded data formats.
