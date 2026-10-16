# Group Collaboration Agent Skill — Protocol 0.1

This file teaches a local AI coding agent how to respond to the human shorthand commands defined by the assignment. The commands are **human-to-agent prompt commands**, not merely aliases for one shell command.

Important boundary:
- Use Git and `groupctl` for mechanical facts and repeatable operations.
- Use AI reasoning for conflict resolution, semantic compatibility, adequacy of tests, and human-check decisions.
- Never treat a successful Git merge as proof that the combined software is correct.
- Never silently discard unfinished local work, force-push shared branches, overwrite tests to make code pass, or arbitrarily choose one side of a conflict.

## SYNC

When the human says `SYNC`:
1. Protect/record current work state.
2. Use `groupctl sync` to fetch remote state and discover structured collaboration events.
3. Inspect new change/review/test/integration/blocker events relevant to current work.
4. Compare the current branch with the configured canonical branch.
5. Merge/update from the canonical branch only when safe. `groupctl sync --merge-canonical` may be used after that judgment.
6. If synchronization changes code, run at least quick validation.
7. Explain what changed, what was incorporated, what remains separate, and whether assumptions were invalidated.
8. Do not automatically merge unrelated feature branches.

## PUB

When the human says `PUB`:
1. Inspect local changes and identify one coherent contribution.
2. Refuse accidental secrets, generated junk, or obviously broken state.
3. Run appropriate quick checks.
4. Publish from the person's branch `people/<person_id>/<topic>`.
5. Use `groupctl pub` with an accurate summary, selected paths, tests, interface changes, known issues, and whether integration is proposed.
6. Report the generated `change_id`.
7. `PUB` does not mean merge into the canonical branch.

## INT <change_id>

When the human says e.g. `INT C-17`:
1. Perform SYNC-level discovery.
2. Locate the change event and inspect its actual Git diff/commit.
3. Identify interface and assumption changes.
4. Use `groupctl integrate C-17` for mechanical integration when safe.
5. If Git conflicts, inspect both intents. Resolve only when the intended combined behavior is clear; otherwise publish/raise a blocker.
6. Build/test and perform semantic review.
7. Record the integration result. After manual conflict resolution use `groupctl integration-result`.

## REV <change_id>

When the human says e.g. `REV C-22`:
1. Locate the published change.
2. Inspect its actual diff and surrounding accepted code.
3. Check interfaces, assumptions, regressions, missing cases, brittle assumptions, unnecessary complexity, and evidence.
4. Run relevant tests when practical.
5. Distinguish definite defects from risks/preferences.
6. Publish exactly one verdict using `groupctl review`: `accept`, `accept_with_changes`, `reject`, or `needs_more_evidence`.
7. Do not merge merely because the review is positive.

## TEST [scope]

When the human says `TEST`, `TEST current`, `TEST main`, or `TEST C-17`:
1. Resolve the requested scope and exact commit.
2. Discover applicable build/tests/static/UI checks.
3. Run applicable automated checks.
4. Investigate failures enough to distinguish code failure, broken/outdated tests, missing environment, or inconclusive evidence.
5. Never rewrite expected output merely to make tests pass.
6. Identify concrete human-required checks.
7. Use `groupctl test` to publish a `test_result` event.
8. Explain what is proven, failed, and still unknown.

## STATUS

When the human says `STATUS`:
1. Use `groupctl status` to inspect identity, group, branch, commit, uncommitted work, canonical commit, ahead/behind relation, tests and blockers.
2. Inspect relevant events if needed.
3. Recommend one concise next action.
4. Do not perform a large merge or rewrite merely because STATUS was requested.

## FINAL

When the human says `FINAL`:
1. Synchronize group metadata and canonical state.
2. Identify the exact canonical commit proposed as the group result.
3. Identify published changes not represented in the accepted state and determine whether they are obsolete/rejected/important/unknown.
4. Review latest reviews, blockers and test evidence.
5. Run final validation and identify human-required checks.
6. If important unresolved work or failures remain, do not silently finalize.
7. Use `groupctl final prepare` to produce a proposed candidate and show it to the human.
8. Only after human confirmation use `groupctl final complete --confirm`.
9. Report the exact canonical commit and final report path.
10. FINAL is group finalization, not personal-branch submission.
