# ICM Workspace Blueprint — Functional Specification

A general-purpose specification for building ICM (Interpretable Context
Methodology) workspaces for any project type. Based on the Van Clief/
McDermott paper and Jake Van Clief's video walkthrough, refined through
practical implementation.

Give this document to an AI agent along with a description of your
project to generate a complete ICM workspace scaffold.

---

## Core Principles

1. **Folder structure as architecture** — the folder IS the system.
   No frameworks, no databases, no orchestration code.
2. **Plain text as interface** — markdown and JSON. Universal,
   editable, version-controllable.
3. **Layered context loading** — only load what you need for the
   current task. Never dump everything.
4. **Every output is an edit surface** — humans can modify any
   file between stages.
5. **Configure the factory, not the product** — set up the workspace
   once, produce new deliverables using the same configuration.

**When NOT to use ICM:** Real-time multi-agent collaboration
(needs message-passing), high-concurrency systems (needs deployment
infrastructure), complex automated branching logic (needs code).
ICM works best for sequential workflows with human review at each
step and repeatable pipelines.

---

## Five-Layer Hierarchy

| Layer | File | Question Answered | Token Budget | Contains |
|-------|------|-------------------|-------------|----------|
| 0 | CLAUDE.md* | "Where am I?" | ~800 tok | Folder map, naming conventions, file placement rules, quick nav |
| 1 | CONTEXT.md (root) | "Where do I go?" | ~300 tok | Task routing table with "You'll Also Need" column |
| 2 | workspace/CONTEXT.md | "What do I do?" | 200-500 tok | Load/skip tables, skills, hard rules, sub-routing |
| 3 | workspace/docs/*.md | "What rules apply?" | 500-2k tok | Stable reference material — internalize as constraints |
| 4 | workspace/output/ | "What am I working with?" | varies | Per-run working artifacts — process as input |

*\*CLAUDE.md is a Claude Code convention — it auto-loads this file
every session. For other tools, rename to match their convention
or load manually. The methodology is model-agnostic.*

**Layer 3 vs Layer 4 — critical distinction:**
- Layer 3: does NOT change between runs. Model should INTERNALIZE as constraints. Lives in docs/.
- Layer 4: DOES change between runs. Model should PROCESS as input. Lives in output/.
- Structural separation in folders is what gives the model the signal.

---

## Discovery Process

Before building a scaffold, answer these questions about the project:

### 1. What are the siloed areas of work?

Each area becomes a workspace (folder with its own CONTEXT.md).
A workspace is a DIFFERENT KIND of work, not a different phase
of the same work.

Examples:
- Content production: writing-room, production, community
- Trading research: lab (experiments), bench (validation), deploy (monitoring)
- Software: design, build, test, deploy

Ask: "If I'm working in area A, do I need to know about area B?"
If no — they're separate workspaces. Most projects need 2-4
workspaces. More than 5 suggests some are better as sub-areas
within a workspace (like a pipeline inside a workspace).

### 2. What flows between workspaces?

The cross-workspace flow is one-way. Outputs of upstream workspaces
become inputs of downstream workspaces through file handoff.

Ask: "What artifact does workspace A produce that workspace B needs?"
That's the handoff.

### 3. What file types does the project produce?

Each distinct artifact gets a type slug in the naming convention.
Think about: drafts, finals, configs, reports, logs, builds.

Ask: "If I listed every kind of file this project creates, what
would the list look like?"

### 4. What are the stable rules vs per-run inputs?

Stable rules → Layer 3 (docs/). Style guides, conventions, standards,
templates, reference material.

Per-run inputs → Layer 4 (output/). Results, drafts, builds, logs,
anything that changes each time you run.

### 5. What tools and skills are used?

Each tool/skill gets wired into a workspace CONTEXT.md with:
- Activation type: ALWAYS-ON, STAGE TRIGGER, or ON-DEMAND
- When: which stage or task triggers it
- Purpose: what it does there

### 6. Are there existing external tools or workspaces?

Complex external tools (compilers, specialized agents, build
systems) stay in their own workspace. The scaffold references
them as ON-DEMAND skills. The external tool reads the scaffold's
CLAUDE.md for naming rules and writes files directly to the
scaffold. One file, one location — no duplicates.

Ask: "What tools already exist that this project needs to use?
Do they have their own folder structure or workspace?"

### 7. What must never happen?

These become hard rules in workspace CONTEXT.md files and
automated enforcement in git hooks.

---

## Build Checklist

Read this before the templates below. It's the execution order.

1. [ ] Answer the 7 discovery questions above
2. [ ] Create folder structure (workspaces, docs/, output/, config)
3. [ ] Write CLAUDE.md (Layer 0) — map, naming, placement, nav
4. [ ] Write CONTEXT.md (Layer 1) — router with task/destination/needs
5. [ ] Write workspace CONTEXT.md files (Layer 2) — rules, load/skip, skills
6. [ ] Write stage contracts if workspace has a pipeline
7. [ ] Create Layer 3 doc stubs (section headers, fill later)
8. [ ] Create infrastructure READMEs (harness, data, etc.)
9. [ ] Create config files
10. [ ] Create audit log (empty, append-only)
11. [ ] Set up git hooks for automated enforcement
12. [ ] Add .gitkeep to empty output/ directories
13. [ ] Verify: every file referenced in a CONTEXT.md exists
14. [ ] Verify: every existing file is referenced by a CONTEXT.md
15. [ ] Verify: operative instructions are front-loaded (not buried at end)
16. [ ] Init git, commit scaffold

---

## Templates

The following templates correspond to build checklist steps 3-8.

---

## CLAUDE.md Template (Layer 0)

Front-load operative instructions. Reference tables go in the middle.
Structural map goes at the end.

```markdown
# [Project Name] — Workspace Map

last_reviewed: YYYY-MM-DD | review_cadence: [quarterly|monthly]

## What This Is

[One paragraph: what this workspace system does, how many workspaces,
what the agent should know immediately.]

**CONTEXT.md** (top-level) routes you to the right workspace.
This file is the map.

---

## File Discipline

Files belong in the workspace where they're used:
- [workspace A purpose] → [workspace A]/
- [workspace B purpose] → [workspace B]/
- [workspace C purpose] → [workspace C]/

If you're about to create a file in a workspace that doesn't
match the file placement rules, stop and check this file.

**Before creating any file:**
1. Filename matches the naming pattern below
2. Type exists in the type catalog
3. File lands in the correct workspace per placement rules
4. If type is NOT in the catalog, flag it before creating

**Before finishing a session:** If you created a new file type,
added a skill, or changed a workflow:
1. Add the new type to this file's type catalog and placement rules
2. Update the workspace CONTEXT.md to reflect the change

---

## Automated Enforcement

[List any git hooks or automated checks. What they block, what
they warn about.]

---

## ID & Naming Conventions

### File Name Pattern

`[segments]-[separated]-[by]-[dashes].[ext]`

[Define the segments. Each segment is a dimension of identity:
project, component, type, status, version, date — whatever makes
files self-identifying without folders.]

| Segment | Values | Required |
|---------|--------|----------|
| ... | ... | ... |

### Type Catalog

| Type Slug | What It Is |
|-----------|-----------|
| ... | ... |

**Statuses:** [define the status progression, e.g., draft → review → final]

### Adding a new [entity]

[How to add a new project, component, archetype, etc. Should
require no folder creation — just start naming files.]

---

## File Placement Rules

### [Workspace A]
- **[type]:** `[workspace]/[naming-pattern]`
- ...
- **Ready for [workspace B]:** Copy [artifact] to `[workspace B]/output/`

### [Workspace B]
- ...

### [Workspace C]
- ...

---

## Folder Structure

[Annotated tree showing the full workspace layout]

---

## Quick Navigation

| Want to... | Go here |
|------------|---------|
| ... | ... |

---

## Cross-Workspace Flow

[ASCII diagram showing one-way flow between workspaces]

**Handoff protocol:** When files cross workspace boundaries, copy
them to the destination workspace's output/ folder. The source
file stays in place. Each workspace reads only from its own
output/ or from shared config.
```

---

## CONTEXT.md Template (Layer 1)

```markdown
# [Project Name] — Task Router

## What This Is

[One paragraph. Reference CLAUDE.md for the map.]

**Resuming work?** [Tell the agent how to check status —
e.g., name a component and the agent checks what files exist.]

**Periodic health check:** Ask the agent to review for recurring
patterns. This can be scoped to a component or scaffold-wide.
When a pattern emerges, fix the source file directly.

---

## Task Routing

| Your Task | Go Here | You'll Also Need |
|-----------|---------|------------------|
| ... | `[workspace]/CONTEXT.md` | [cross-workspace resources] |

---

## Workspace Summary

| Workspace | Purpose | Skills & Tools |
|-----------|---------|----------------|
| ... | ... | ... |

---

## Cross-Workspace Flow

[Same diagram as CLAUDE.md — intentional duplication]
```

---

## Workspace CONTEXT.md Template (Layer 2)

Front-load hard rules. Load/skip table next. Routing and structure
at the end.

```markdown
# [Workspace Name]

last_reviewed: YYYY-MM-DD | review_cadence: [quarterly|monthly]

## What This Workspace Is

[One paragraph: what happens here, what's upstream, what's downstream]

---

## Hard Rules

[Numbered list of workspace-specific rules. These are the most
important instructions — front-loaded so the model reads them first.]

---

## What to Load

**Always load first:** [key file, e.g., journal, project brief]

| Task | Layer 3 (internalize) | Layer 4 (process) | Skip |
|------|----------------------|-------------------|------|
| ... | `docs/[file]` | `output/[file]` | [what to ignore] |

---

## Where to Go

| You Want To... | Go Here |
|----------------|---------|
| ... | ... |

**Don't read everything.** Identify your task, load only what you need.

---

## Skills & Tools

| Skill / Tool | Activation | When | Purpose |
|-------------|-----------|------|---------|
| ... | ALWAYS-ON / STAGE TRIGGER / ON-DEMAND | ... | ... |

---

## [Workspace-specific sections]

[Procedures, sub-routing to pipelines, handoff protocols, etc.]

---

## Folder Structure

[Annotated tree of this workspace only]
```

---

## Stage Contract Template (Layer 2, inside a pipeline)

```markdown
# Stage [N]: [Name]

## Inputs

- **Layer 3 (internalize):** [reference docs]
- **Layer 4 (process):** [artifacts from prior stages]

## Process

[What to do. One clear instruction set.]

## Outputs

- [What this stage produces, where it goes]
- [Journal/audit entries]
- [Handoff to next stage or workspace]

[Human approval signal: how the agent knows approval happened,
e.g., "frozen file exists = approved"]
```

---

## Infrastructure Directory Template

For directories that are shared infrastructure (not workspaces):

```markdown
# [Directory Name]

[One line: what this is. Starts empty.]

**When you add or change a file in this directory, update this README.**

## When files appear here

[What triggers content being added to this directory]

## What might live here

[Expected file types, naming rules if different from main convention]
```

---

## Patterns Catalog

Reusable patterns discovered during implementation:

### Journal Pattern
One append-only narrative file per project/component that captures
decisions, reasoning, and outcomes. Hard rule: journal every
significant decision. Create on first entry. Always load at
session start.

### Rolling Window Pattern
For projects with time-series data that shifts over time: use
named windows (W1, W2, W3) with assigned roles (calibration,
holdout, future). When data rolls, change the role assignment
in config — no file renames, no structural changes. Lock flags
are per-window. Skip this pattern if your project doesn't have
rolling time periods.

### Self-Updating README Pattern
Infrastructure directories (harness, data, scoring, tests) get
a README with "When you add or change a file in this directory,
update this README." Content documents itself as it emerges.

### External Tool Integration
Complex external tools stay in their own workspace. Wire them
into the scaffold via the skills table with ON-DEMAND activation.
The external tool reads the scaffold's CLAUDE.md for naming rules
and writes files directly to the scaffold. One file, one location.

### Status Check via File Existence
No notification system needed. The agent checks what files exist
across workspace output/ directories to report current state.
The naming convention with status suffixes encodes the lifecycle.

### Healthcheck Pattern
The router (CONTEXT.md) includes a healthcheck instruction. On
request, the agent reads journals, audit logs, and CONTEXT.md
files to surface recurring patterns. When a pattern emerges,
fix the source file directly (edit-source principle).

### Human as Router
Forward flow happens through files and handoff protocol. Backward
flow (iteration, rework) happens through the human. The human
reads downstream output, goes to the upstream workspace, and tells
the agent what to work on. The journal carries the narrative.

