# Plan: ICM Workspace Scaffold for Futures Trading Research

## Context

Ji wants to start fresh with a clean workspace organized using ICM (Interpretable Context Methodology) principles from the Van Clief/McDermott paper and Jake Van Clief's video walkthrough. This is a greenfield scaffold — no migration of existing files. The workspace supports iterative futures strategy research across multiple archetypes and instruments.

The five-layer hierarchy:
- Layer 0: CLAUDE.md (~800 tok) — "Where am I?" — folder map, naming conventions, file placement
- Layer 1: CONTEXT.md (~300 tok) — "Where do I go?" — task routing table
- Layer 2: Workspace/Stage CONTEXT.md (200-500 tok) — "What do I do?" — inputs/process/outputs
- Layer 3: docs/ and references/ (500-2k tok) — "What rules apply?" — stable reference material
- Layer 4: output/ (varies) — "What am I working with?" — per-run working artifacts

---

## Deliverables

### 1. Folder Structure

```
pipeline/
├── CLAUDE.md                                  ← Layer 0
├── CONTEXT.md                                 ← Layer 1
│
├── lab/                                       ← Workspace: experiments
│   ├── CONTEXT.md                                Layer 2 (workspace entry)
│   ├── docs/                                     Layer 3 (reference)
│   │   ├── simulation-rules.md
│   │   ├── feature-rules.md
│   │   └── exit-templates.md
│   ├── output/                                   Layer 4 (working artifacts)
│   └── workflows/                                Sub-pipeline
│       ├── CONTEXT.md                               Pipeline router
│       ├── 01-features/
│       │   ├── CONTEXT.md                           Layer 2 contract
│       │   ├── references/                          Layer 3
│       │   └── output/                              Layer 4
│       ├── 02-hypotheses/
│       │   ├── CONTEXT.md
│       │   ├── references/
│       │   └── output/
│       └── 03-params/
│           ├── CONTEXT.md
│           ├── references/
│           └── output/
│
├── bench/                                     ← Workspace: validation
│   ├── CONTEXT.md                                Layer 2
│   ├── docs/                                     Layer 3
│   │   └── statistical-gates.md
│   └── output/                                   Layer 4
│
├── deploy/                                    ← Workspace: build & monitor
│   ├── CONTEXT.md                                Layer 2
│   ├── docs/                                     Layer 3
│   └── output/                                   Layer 4
│
├── _config/                                   ← Layer 3 (global, shared)
│   ├── instruments.md
│   └── period-config.md
│
├── harness/                                   ← Fixed engines (never edit)
│   ├── backtest_engine.py
│   ├── evaluate_features.py
│   └── hypothesis_generator.py
│
├── data/                                      ← Source bar/touch data
├── scoring/                                   ← Scoring adapters + models
├── audit/                                     ← Append-only log
│   └── audit_log.md
└── tests/
```

---

### 2. CLAUDE.md (Layer 0 — The Map)

Purpose: Folder structure, naming conventions, file placement rules, quick nav.
Budget: ~800 tokens. Loaded every conversation.
Does NOT contain: experiment instructions, simulation rules, pipeline details.

Sections:
1. **What This Is** — one paragraph (Ji's futures strategy research workspace, three siloed workspaces)
2. **Folder Structure** — annotated tree (as above)
3. **Quick Navigation** — task → go-here table
4. **Cross-Workspace Flow** — lab → bench → deploy (one-way)
5. **ID & Naming Conventions** — full pattern table + type catalog
6. **File Placement Rules** — per-workspace paths with naming patterns
7. **Statuses** — draft → frozen → validated → deployed

#### ID & Naming Conventions (full spec)

**File Name Pattern:**

`[arch]-[instrument]-[type]-[status].[ext]`

| Segment | Values | Required |
|---------|--------|----------|
| arch | `rotational`, `zone-touch`, or new slug | yes |
| instrument | `NQ`, `ES`, `GC`, or new from `_config/instruments.md` | yes |
| type | see type catalog | yes |
| status | `draft`, `frozen`, `validated`, `deployed` | when applicable |
| ext | `.py`, `.json`, `.tsv`, `.csv`, `.md`, `.cpp` | yes |

**Type Catalog:**

| Type Slug | What It Is |
|-----------|-----------|
| `feature-engine` | Feature computation code |
| `simulator` | Strategy simulator (do not edit) |
| `evaluator` | Feature screening harness (do not edit) |
| `hypothesis-configs` | Parameter definitions |
| `trend-defense` | Risk filter |
| `results` | Experiment log (append-only) |
| `findings` | Analysis doc (numbered findings) |
| `frozen-features` | Locked feature set |
| `frozen-params` | Locked exit parameters |
| `hypothesis` | Promoted hypothesis config |
| `p2-tradelog` | Holdout run output |
| `stress-montecarlo` | Monte Carlo stress test |
| `stress-slippage` | Slippage sweep |
| `stress-kelly` | Kelly criterion / ruin prob |
| `verdict` | Statistical pass/fail |
| `build` | Deployment C++ |
| `paper-trades` | Live paper trade log |
| `drift` | Drift monitoring report |

**Statuses:** `draft` → `frozen` → `validated` → `deployed`

#### File Placement Rules (full spec)

##### Lab (experiments)
- **Feature engines:** `lab/[arch]-[inst]-feature-engine.py`
- **Simulators:** `lab/[arch]-[inst]-simulator.py`
- **Evaluators:** `lab/[arch]-[inst]-evaluator.py`
- **Hypothesis configs:** `lab/[arch]-[inst]-hypothesis-configs.py`
- **Risk filters:** `lab/[arch]-[inst]-trend-defense.py`
- **Experiment results:** `lab/output/[arch]-[inst]-results.tsv`
- **Findings:** `lab/output/[arch]-[inst]-findings.md`
- **Frozen features:** `lab/output/[arch]-[inst]-frozen-features-frozen.json`
- **Frozen params:** `lab/output/[arch]-[inst]-frozen-params-frozen.json`
- **Frozen hypothesis:** `lab/output/[arch]-[inst]-hypothesis-frozen.json`
- **Ready for validation:** Copy frozen configs to `bench/output/`

##### Bench (validation)
- **P2 trade log:** `bench/output/[arch]-[inst]-p2-tradelog.csv`
- **Stress tests:** `bench/output/[arch]-[inst]-stress-[type].md`
- **Verdicts:** `bench/output/[arch]-[inst]-verdict-validated.json`
- **Ready for deployment:** Copy verdict + frozen params to `deploy/output/`

##### Deploy (build & monitor)
- **C++ builds:** `deploy/output/[arch]-[inst]-build-v[n]-deployed.cpp`
- **Paper trades:** `deploy/output/[arch]-[inst]-paper-trades.csv`
- **Drift reports:** `deploy/output/[arch]-[inst]-drift-[YYYY-MM].md`

##### Adding a new archetype or instrument
No folders to create. Register in `_config/instruments.md`, start naming files.

---

### 3. CONTEXT.md (Layer 1 — The Router)

Purpose: Route agents to the right workspace. ONE job.
Budget: ~300 tokens. Short.
Does NOT contain: detailed instructions, file placement rules, naming conventions.

Sections:
1. **What This Is** — one paragraph
2. **Task Routing** — Your Task | Go Here | You'll Also Need
3. **Workspace Summary** — Workspace | Purpose | Skills & Tools
4. **Cross-Workspace Flow** — same diagram as CLAUDE.md (intentional duplication)

Task Routing table:

| Your Task | Go Here | You'll Also Need |
|-----------|---------|------------------|
| Screen features | `lab/CONTEXT.md` | `_config/instruments.md` |
| Test hypotheses | `lab/CONTEXT.md` | `_config/period-config.md` |
| Sweep parameters | `lab/CONTEXT.md` | `lab/docs/simulation-rules.md` |
| Analyze results | `lab/CONTEXT.md` | `audit/audit_log.md` |
| Run P2 holdout | `bench/CONTEXT.md` | Frozen params from `lab/output/` |
| Stress test | `bench/CONTEXT.md` | `bench/docs/statistical-gates.md` |
| Get verdict | `bench/CONTEXT.md` | — |
| Generate C++ | `deploy/CONTEXT.md` | `scoring/`, frozen params |
| Monitor live | `deploy/CONTEXT.md` | Verdict from `bench/output/` |
| Look up instruments | `_config/instruments.md` | — |
| Review audit | `audit/audit_log.md` | — |

---

### 4. lab/CONTEXT.md (Layer 2 — The Room)

Purpose: What to do when working in the lab workspace.
Sections:
1. **What This Workspace Is** — one paragraph (upstream of everything)
2. **Where to Go** — sub-routing table within lab
3. **Folder Structure** — lab-only tree
4. **What to Load** (Inputs table with Layer 3 vs Layer 4 distinction):

| Task | Layer 3 (internalize) | Layer 4 (process) | Skip |
|------|----------------------|-------------------|------|
| Feature screening | `docs/feature-rules.md`, `_config/instruments.md` | `output/[arch]-[inst]-results.tsv` | hypothesis-configs, exit-templates, other archetypes |
| Hypothesis testing | `docs/simulation-rules.md`, `_config/period-config.md` | `output/[arch]-[inst]-frozen-features-frozen.json` | feature code, other archetypes |
| Param sweep | `docs/exit-templates.md`, `docs/simulation-rules.md` | `output/[arch]-[inst]-results.tsv` | feature code, other archetypes |
| Analyze results | — | `output/[arch]-[inst]-results.tsv`, `output/[arch]-[inst]-findings.md` | all code files |

5. **Skills & Tools** — with WHEN (stage) and WHY (purpose):

| Skill / Tool | When | Purpose |
|-------------|------|---------|
| `harness/backtest_engine.py` | 03-params | Fixed engine — pass config, get PF |
| `harness/evaluate_features.py` | 01-features | Fixed evaluator — pass archetype, get spread |
| `harness/hypothesis_generator.py` | 02-hypotheses | Fixed generator — pass config, get P1/P1b |

6. **Hard Rules** — workspace-specific:
   - One change per experiment
   - Entry-time only (features computable at entry bar)
   - P1b replication required
   - Constants from `_config/instruments.md`, never hardcode
   - Never edit harness/ files or simulators
   - Archetype header: line 1 `# archetype: {name}`
   - Log every experiment to results.tsv, every promotion to audit_log.md

---

### 5. lab/workflows/CONTEXT.md (Pipeline Router)

Routes to the three experiment stages. Short — just a sub-routing table.

| Stage | What Happens | Entry From |
|-------|-------------|------------|
| 01-features | Screen features on P1 | New archetype registered |
| 02-hypotheses | Test hypothesis configs on P1 | Frozen features from 01 |
| 03-params | Optimize exit params on P1 | Promoted hypothesis from 02 |

---

### 6. lab/workflows/01-features/CONTEXT.md (Stage Contract)

Layer 2 contract — Inputs/Process/Outputs format:

```
## Inputs
- Layer 3: lab/docs/feature-rules.md
- Layer 3: _config/instruments.md
- Layer 4: lab/output/[arch]-[inst]-results.tsv (prior runs, if exists)

## Process
Edit [arch]-[inst]-feature-engine.py. One feature change per experiment.
Run: python harness/evaluate_features.py --archetype [arch]
Metric: best-bin vs worst-bin spread on P1.
Keep rule: spread > threshold → keep. Else revert.
Budget: 300 experiments per IS period.

## Outputs
- Append row to lab/output/[arch]-[inst]-results.tsv
- When human approves: lab/output/[arch]-[inst]-frozen-features-frozen.json
```

### 7. lab/workflows/02-hypotheses/CONTEXT.md (Stage Contract)

```
## Inputs
- Layer 3: lab/docs/simulation-rules.md
- Layer 3: _config/period-config.md
- Layer 4: lab/output/[arch]-[inst]-frozen-features-frozen.json

## Process
Edit [arch]-[inst]-hypothesis-configs.py. One param change per experiment.
Run: python harness/hypothesis_generator.py --archetype [arch]
Metric: P1 PF at 3t cost, min 30 trades.
Keep rule: PF improves by > 0.1 → keep. Else revert.
Must pass P1a AND P1b replication.
Budget: 200 experiments per archetype per IS period.

## Outputs
- Append row to lab/output/[arch]-[inst]-results.tsv
- When human approves: lab/output/[arch]-[inst]-hypothesis-frozen.json
```

### 8. lab/workflows/03-params/CONTEXT.md (Stage Contract)

```
## Inputs
- Layer 3: lab/docs/simulation-rules.md, lab/docs/exit-templates.md
- Layer 3: _config/instruments.md
- Layer 4: lab/output/[arch]-[inst]-hypothesis-frozen.json

## Process
Edit exit params config. One param change per experiment.
Run: python harness/backtest_engine.py --config [config]
Metric: P1 PF at 3t, min 30 trades.
Keep rule: PF improves by > 0.05 → keep. Else revert.
Budget: 500 experiments per archetype per IS period.

## Outputs
- Append row to lab/output/[arch]-[inst]-results.tsv
- When human approves: lab/output/[arch]-[inst]-frozen-params-frozen.json
- Ready for bench: copy frozen params to bench/output/
```

---

### 9. bench/CONTEXT.md (Layer 2)

Sections:
1. **What This Workspace Is** — validation and judgment, downstream of lab
2. **Where to Go** — P2 holdout, stress test, verdict
3. **What to Load:**

| Task | Layer 3 (internalize) | Layer 4 (process) | Skip |
|------|----------------------|-------------------|------|
| P2 holdout | `docs/statistical-gates.md`, `_config/instruments.md` | `[arch]-[inst]-frozen-params-frozen.json` from lab | All lab code |
| Stress test | `docs/statistical-gates.md` | `[arch]-[inst]-p2-tradelog.csv` | Lab, deploy |
| Verdict | `docs/statistical-gates.md` | `[arch]-[inst]-p2-tradelog.csv`, stress reports | Lab, deploy |

4. **Hard Rules:**
   - P2 is ONE SHOT. If `holdout-locked.flag` exists for this arch+inst, stop.
   - Never re-run P2 with different params.
   - Audit log is append-only.
   - Verdicts are deterministic — no judgment calls, just gates.

---

### 10. deploy/CONTEXT.md (Layer 2)

Sections:
1. **What This Workspace Is** — build shop and monitoring, downstream of bench
2. **Where to Go** — generate C++, monitor paper trades, check drift
3. **What to Load:**

| Task | Layer 3 (internalize) | Layer 4 (process) | Skip |
|------|----------------------|-------------------|------|
| Generate C++ | `docs/acsil-reference.md` | Frozen params + verdict from bench | Lab, bench internals |
| Monitor trades | — | `[arch]-[inst]-paper-trades.csv`, verdict baseline | Lab, bench |
| Drift check | — | `[arch]-[inst]-paper-trades.csv`, `[arch]-[inst]-drift-*.md` | Lab, bench |

4. **Hard Rules:**
   - Human compiles, replays, creates `deployment-ready.flag`
   - Agent never creates deployment flags
   - No code changes in this workspace — assembly and observation only

---

### 11. Reference Docs (Layer 3 stubs)

Create stub files with headers for:
- `lab/docs/simulation-rules.md` — entry, exit, trail, cost mechanics
- `lab/docs/feature-rules.md` — what makes a valid feature, entry-time constraint
- `lab/docs/exit-templates.md` — reference exit structure patterns
- `bench/docs/statistical-gates.md` — PF thresholds, MWU, permutation, percentile gates
- `deploy/docs/acsil-reference.md` — Sierra Chart ACSIL compilation notes
- `_config/instruments.md` — NQ/ES/GC: tick size, tick value, sessions, costs
- `_config/period-config.md` — P1/P2 date ranges per archetype

---

## Implementation Steps

1. Create the folder structure (mkdir -p for all directories)
2. Write CLAUDE.md (Layer 0)
3. Write CONTEXT.md (Layer 1)
4. Write lab/CONTEXT.md (Layer 2)
5. Write lab/workflows/CONTEXT.md (pipeline router)
6. Write lab/workflows/01-features/CONTEXT.md (stage contract)
7. Write lab/workflows/02-hypotheses/CONTEXT.md (stage contract)
8. Write lab/workflows/03-params/CONTEXT.md (stage contract)
9. Write bench/CONTEXT.md (Layer 2)
10. Write deploy/CONTEXT.md (Layer 2)
11. Create Layer 3 reference doc stubs
12. Create _config/ files (instruments.md, period-config.md)
13. Create audit/audit_log.md (empty, append-only)
14. Create .gitkeep files in empty output/ directories

## Verification

1. Ask Claude "read the CLAUDE.md and tell me what this is" — should describe the workspace, workspaces, naming conventions
2. Ask Claude "I want to run feature experiments" — should route to lab/CONTEXT.md, load correct Layer 3/4 files
3. Ask Claude "I want to validate P2" — should route to bench/CONTEXT.md, ask for frozen params
4. Create a test file — Claude should name it correctly per naming convention and place it in the right output/ folder
5. Token check: CLAUDE.md should be under 200 lines, CONTEXT.md under 50 lines
