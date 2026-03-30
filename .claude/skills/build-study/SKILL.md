# SKILL: Build ACSIL Study

version: 1.0
last_reviewed: 2026-03-29

## Purpose

Package frozen parameters, instrument config, simulation rules, and
existing study code from the futures pipeline, then compile and
verify the C++ study using the ACSIL build system. The result is a
compiled, named study written directly to `lab/`.

## Trigger Conditions

Use this skill when the user:
- Says "build study", "compile study", or "build cpp"
- Asks to create or update a C++ ACSIL study from frozen params
- Asks to translate Python results to a SierraChart study
- References `/build-study`

## Required Inputs

The user must specify:
- **Archetype** (e.g., `rotational`, `zone-touch`)
- **Instrument** (e.g., `NQ`)

Optional:
- **Variant** (e.g., `chop`, `rangefade`) — omit for primary study
- **Intent** — new study, iterate existing, or translate from Python

## Execution Sequence

### Phase 1: Gather Context (futures pipeline)

Read these files from the pipeline. Stop and report if any required
file is missing.

| File | Required | Purpose |
|------|----------|---------|
| `_config/instruments.md` | yes | Tick size, tick value, cost, session times |
| `_config/period-config.md` | yes | Calibration/holdout windows |
| `lab/docs/simulation-rules.md` | yes | Entry/exit/trail/cost mechanics |
| `lab/output/[arch]-[inst]-params-frozen.json` | if exists | Frozen parameters to implement |
| `lab/output/[arch]-[inst]-features-frozen.json` | if exists | Frozen feature set |
| `lab/output/[arch]-[inst]-journal.md` | yes | Strategy narrative + decisions |
| `lab/[arch]-[inst]-study[-variant].cpp` | if iterating | Existing study to modify |
| `lab/[arch]-[inst]-simulator.py` | if translating | Python implementation to port |

Print a summary of what was gathered:
- Archetype + instrument + variant
- Frozen params: found / not found
- Existing study: found (line count) / not found (new build)
- Intent: new / iterate / translate

### Phase 2: Compile (ACSIL workspace)

The study must compile at `C:\Projects\sierrachart` using the ACSIL
build system.

**Write the .cpp source file to:**
`C:\Projects\sierrachart\studies\workspace\[StudyName].cpp`

StudyName follows SC convention (e.g., `ATEAM_ROTATION_V3_CHOP`).
This is the SC-internal name — different from the pipeline name.

**Compile:**
```bash
cd /c/Projects/sierrachart && bash build.sh studies/workspace/[StudyName].cpp
```

**On compile failure:** Read error output, fix the .cpp, recompile.
Iterate until 0 warnings, 0 errors.

**ACSIL rules** (read `C:\Projects\sierrachart\CLAUDE.md` for full list):
- `#include "sierrachart.h"` (lowercase) must be first include
- Never create alternative build directories
- Edit in place on iteration, never create copies

### Phase 3: Place Result (futures pipeline)

After successful compilation:

1. **Copy the .cpp** to the pipeline with correct naming:
   `lab/[arch]-[inst]-study[-variant].cpp`

   The pipeline file is the **authoritative copy**. The SC workspace
   copy is for compilation only.

2. **Verify naming** against CLAUDE.md rules:
   - Segments: `[arch]-[inst]-study[-variant].cpp`
   - Type `study` is in the type catalog
   - File lands in `lab/` per placement rules

3. **Append to journal** (`lab/output/[arch]-[inst]-journal.md`):
   ```
   ### YYYY-MM-DD — Study Build
   - Intent: [new / iterate / translate]
   - Study: [filename]
   - SC name: [ATEAM_..._name]
   - Params source: [frozen json path or "manual"]
   - Compile: PASS (0 warnings)
   - Changes: [brief description of what changed]
   ```

4. **Append to audit log** (`audit/audit_log.md`):
   ```
   YYYY-MM-DD [arch] [inst] STUDY_BUILD — [filename] compiled,
   placed in lab/. Source: [frozen params / iteration / translation].
   ```

### Phase 4: Verification Prompt

After placement, remind the user:
- "Study placed at `lab/[arch]-[inst]-study[-variant].cpp`"
- "To verify Python ↔ C++ match: see `lab/workflows/verify/CONTEXT.md`"
- "To hand off to bench: freeze params + verify report must both exist"

Do NOT auto-run verification. The user decides when to verify.

---

## Study .cpp Requirements

Every study built by this skill must include:

1. **Archetype header comment:** `// archetype: [name]`
2. **Study metadata block:**
   ```cpp
   // @study    [arch]-[inst]-study[-variant]
   // @version  [n]
   // @type     ACSIL strategy study
   // @summary  [one line]
   ```
3. **Constants from registry** — tick size, cost, session times must
   match `_config/instruments.md`. Never hardcode.
4. **Frozen params** — if params-frozen.json exists, all values must
   match. Use `sc.Input[N]` with documented mapping.
5. **Version log** — append changelog entry on iteration.

---

## Anti-Patterns (never do these)

- Do NOT compile at any location other than `C:\Projects\sierrachart`
- Do NOT leave the authoritative .cpp in the SC workspace — always
  copy back to `lab/`
- Do NOT hardcode tick size, cost, or session times in the study
- Do NOT skip the journal entry
- Do NOT skip the audit log entry
- Do NOT auto-run verification — prompt the user
- Do NOT create a study without checking CLAUDE.md naming rules first
- Do NOT modify simulator.py or evaluator.py files (harness code)

---

## Self-Check (run before finishing)

- [ ] All required context files read from pipeline
- [ ] Study compiles with 0 warnings, 0 errors
- [ ] .cpp copied to `lab/` with correct naming
- [ ] Archetype header and metadata block present
- [ ] Constants match `_config/instruments.md`
- [ ] Frozen params match (if applicable)
- [ ] Journal entry appended
- [ ] Audit log entry appended
- [ ] User informed about verification next step
