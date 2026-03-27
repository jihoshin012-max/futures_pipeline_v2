# Feature Rules

<!--
Layer 3 reference. Loaded during feature screening (Stage 01).
Defines what makes a valid feature.
-->

## Entry-Time Constraint

All features must be computable at the entry bar. No data from after
the entry point may be used. The evaluator truncates bar data to
enforce this — any violation auto-reverts the experiment.

## Feature Requirements

<!-- Define: data types available, normalization expectations, naming -->

## Prohibited Patterns

<!-- Define: features that look valid but leak future data -->
