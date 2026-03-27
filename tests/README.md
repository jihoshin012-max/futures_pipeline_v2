# Tests

Test suite for harness engines, data loaders, and pipeline contracts.
Starts empty.

**When you add or change a file in this directory, update this README.**

## When tests appear here

When shared code is extracted to harness/ or scoring/, add tests
that verify the interface contracts. Tests ensure that harness
changes don't silently break experiment results.

## What to test

- Engine contracts: given config + data, returns expected structure
- Data loader: given file path, returns expected format
- Scoring adapters: given input, returns expected scores
- Pipeline rules: holdout lock enforcement, audit append-only
