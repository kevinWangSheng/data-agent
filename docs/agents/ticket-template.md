# Ticket template

`/to-tickets` writes each ticket in this shape (`.scratch/<feature>/issues/NN-<slug>.md`). The handoff
fields follow the agentic-delivery research (`report.md:63`): goal, authority, context, evidence, state,
stop conditions, failure handling. Ledger fields are filled in as the ticket moves.

```markdown
# NN — <title>

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

What exists when this is done, in one or two sentences.

## Authority

- May edit: <directories/files>
- Must not edit: acceptance tests at the agreed seam, other tickets' files, `.claude/`, `CODING_STANDARDS.md`
- Server access: none | `da-remote <subcommands>`
- Spend cap: none | $X of model calls

## Context

Links to spec sections, ADRs, CONTEXT terms, upstream files (path:line) to copy from.

## Acceptance

- [ ] Behavior checks at the seam agreed in the spec (tests named here)
- [ ] `make check` passes

## Evidence required

Commands and their real output pasted under Comments: test run, one real run of the changed behavior
(CLI call, single eval task, or screenshot).

## Stop conditions

When to stop and hand back instead of continuing (e.g. spec contradiction, needs a decision, spend cap hit,
second failed review round).

## Failure handling

What to leave behind when stopping: Status `ready-for-human`, a Comment stating what was tried, what failed,
and the branch name.

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: — (accepted | returned)
- Human review minutes: —

## Comments
```
