# Data

Source bar and touch data. Starts with whatever data you onboard.

**When you add or change a file in this directory, update this README.**

## Assumptions

Data in this directory is clean and validated before use.
Validation, schema checks, and regime labeling happen during
data onboarding — outside the experiment/validation/deploy cycle.

## How data gets here

Onboard new data by placing files in this directory. Register
the instrument in `_config/instruments.md` and the date range
in `_config/period-config.md`. The naming and format of data
files will depend on your data source — document the format
when you onboard the first dataset.

## Access

All workspace code accesses data through shared loader code.
When the loader is extracted to harness/ (see harness/README.md),
data access becomes standardized. Until then, archetypes load
data directly.
