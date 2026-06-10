# Maintainer Standards

These standards apply to work in **OrangeBannana/memomatic-pinboard**.

## Repository + Git workflow

- Do all issue tracking, branches, pull requests, and follow-up work in **this fork**: `OrangeBannana/memomatic-pinboard`.
- Create a focused branch for every issue, feature, or maintainer task.
- Branch from the default branch for standalone work.
- If a task grows too large while already in progress, split the extra scope into:
  - a new follow-up issue, and
  - a new branch cut from the active branch only when that dependency is real.
- Keep changes scoped to the issue being worked on. If new work appears during implementation, open a new issue instead of expanding the current one.

## Branching standards

- Prefer clear, searchable branch names such as:
  - `issue-123-short-summary`
  - `feature/short-summary`
  - `task/short-summary`
  - `followup/issue-123-short-summary`
- If a branch depends on unfinished work from another branch, say so in the issue body or handoff note.

## Issue + task organization

Use an issue for every bug, feature request, maintenance task, or follow-up item.

### Recommended labels

Use `/home/runner/work/memomatic-pinboard/memomatic-pinboard/OrangeBannana/memomatic-pinboard/.github/labels.yml` as the source of truth when creating or syncing repository labels.

#### Type

- `type: bug`
- `type: feature`
- `type: task`
- `type: docs`
- `type: maintenance`

#### Area

- `area: app`
- `area: ui`
- `area: deploy`
- `area: docs`
- `area: hardware`

#### Maintainer

- `maintainer: human`
- `maintainer: ai`
- `maintainer: paired`

#### Status

- `status: needs-triage`
- `status: ready`
- `status: in-progress`
- `status: blocked`
- `status: needs-review`
- `status: needs-handoff`
- `status: done`

### Session handoff expectations

When work stops before completion:

- leave a short handoff note in the issue or PR,
- record what was finished,
- record what is still blocked or next,
- add or keep `status: needs-handoff` until someone picks it up.

## Scope control

- Create follow-up issues as soon as unrelated work is discovered.
- Prefer several small linked issues over one drifting “do everything” issue.
- Link dependent issues so future sessions can resume without re-discovery.

## Validation standards

Formal automated tests are tracked separately and should be expanded in another issue. Until then:

- document the validation you performed in the issue or PR,
- run the existing repository checks that apply to the touched files,
- for Python changes, use `python3 -m compileall app deploy.py`,
- for documentation-only changes, manual review is acceptable.

## AI maintainer expectations

- Commit often so work is recoverable.
- Push or report progress with reasonable frequency, especially before context or usage limits.
- Keep commits scoped and descriptive enough that a later human or AI maintainer can resume work quickly.
- If a session is ending with unfinished work, leave handoff notes before stopping.
