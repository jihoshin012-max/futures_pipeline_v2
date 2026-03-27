# Scoring

Scoring models and adapters used by harness engines. Starts empty.

**When you add or change a file in this directory, update this README.**

## When files appear here

When a strategy needs scoring beyond simple PF gating — binned
probability models, ML classifiers, regime-aware routing — the
model artifacts and adapter code go here.

## What might live here

- Adapter code that loads and applies models
- Model weight files (JSON)
- Trained classifiers (pkl — exception to pickle guard)
- Template schemas for new models

## Naming

Per-archetype models: `[arch]-[inst]-scoring-[model-type].[ext]`
Shared models (e.g., regime classifiers): descriptive name with version.

Scoring files don't all follow the main pipeline naming convention
because some models are shared across archetypes.
