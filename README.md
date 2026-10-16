# Group Collaboration Project — Protocol 0.1

This repository contains a working Python implementation of `groupctl`, the deterministic collaboration helper required by the assignment.

## 1. Replace the starter identity values

Edit `.group/group.json`:
- `G-XX` -> your real group ID

Rename `.group/members/PXX.json` to your real person ID, for example `.group/members/P17.json`, and edit:
- `PXX` -> your real person ID
- `YOUR_NAME` -> your display name
- `joined_at` -> an appropriate timestamp

Put the actual programming assignment in `.group/assignment/assignment.md`.

Every teammate should have their own committed file under `.group/members/<person_id>.json`.

## 2. Install locally

From the repository root:

```bash
python -m pip install -e .
```

Then either form works:

```bash
groupctl --help
python -m groupctl --help
```

## 3. Configure this clone's person identity

```bash
groupctl configure --person-id P17
```

The local identity is written under `.group/local/` and is intentionally not authoritative shared state.

## 4. Personal branches

Normal work belongs on:

```text
people/<person_id>/<topic>
```

Example:

```bash
git switch -c people/P17/parser
```

The configured canonical branch (normally `main`) represents accepted group work.

## 5. Useful mechanical commands

These CLI commands support the procedures that the **AI agent** follows when the human types the shorthand prompts `SYNC`, `PUB`, `INT`, `REV`, `TEST`, `STATUS`, and `FINAL`.

### Validate repository structure

```bash
groupctl validate
```

### STATUS support

```bash
groupctl status
```

### SYNC support

```bash
groupctl sync
```

This fetches remote state and discovers immutable `.group/events/*.json` files across remote personal branches. It does **not** blindly merge feature branches.

After the AI judges updating from canonical safe:

```bash
groupctl sync --merge-canonical --quick-test "python -m unittest discover -v"
```

### PUB support

```bash
groupctl pub \
  --summary "Implemented parser support" \
  --path groupctl/events.py \
  --path tests/test_events.py \
  --quick-test "python -m unittest discover -v" \
  --interface "Event validation now checks review verdicts" \
  --propose
```

This commits/pushes the selected work, generates a unique change ID, writes a `change_published` event, then commits/pushes that event.

### Discover events

```bash
groupctl events --refresh
groupctl events --change-id C-1 --json
```

### REV support

After the AI has inspected the real diff and evidence:

```bash
groupctl review C-1 \
  --verdict accept \
  --summary "Implementation and tests look correct" \
  --test "python -m unittest discover -v"
```

### INT support

After the AI inspects the published change:

```bash
groupctl integrate C-1 \
  --test "python -m unittest discover -v" \
  --semantic pass \
  --summary "Integrated C-1 and verified compatibility"
```

If Git reports a conflict, `groupctl` leaves it visible. The AI/human must inspect both sides and resolve deliberately. Then record the result:

```bash
groupctl integration-result C-1 \
  --result success \
  --conflict src/model.py \
  --conflict-summary "Preserved the new parser API and adapted the caller" \
  --build pass --tests pass --semantic pass \
  --summary "Conflict resolved and verified"
```

### TEST support

```bash
groupctl test current \
  --command "python -m unittest discover -v" \
  --human-check "Confirm the interactive UI remains usable"
```

A `test_result` event is committed and pushed.

### Blocker

```bash
groupctl blocker \
  --change-id C-1 \
  --summary "Cannot safely resolve the API conflict" \
  --requested-action "Human decision on the required API behavior"
```

### FINAL support

First prepare a candidate:

```bash
groupctl final prepare
```

The human must inspect `.group/final/candidate.md`.

Only after confirmation:

```bash
groupctl final complete --confirm
```

This creates/publishes `.group/final/final_report.md`, `.group/final/final.json`, and a `final_result` event.

## Important distinction

`SYNC`, `PUB`, `INT C-17`, `REV C-17`, `TEST`, `STATUS`, and `FINAL` are **human-to-agent prompt shorthand commands**. They are not defined as shell aliases. The portable AI instructions are in `AGENT_SKILL.md`.

`groupctl` supplies repeatable mechanics. The AI supplies judgment where mechanics are insufficient, especially conflict resolution, semantic compatibility, test adequacy, and human checks.
