# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Work states (this repo)

After triage, a `ready-for-agent` ticket moves through three more states, recorded on the same `Status:` line (plan §6):

| State         | Set by                     | Meaning                                                                 |
| ------------- | -------------------------- | ----------------------------------------------------------------------- |
| `in-progress` | the agent that claims it   | Claimed; `Claimed by:` and `Branch:` are filled in before any other work |
| `in-review`   | that agent, when finished  | Evidence and ledger filled in; waiting for the human to review the diff |
| `done`        | the human, after merging   | Merged to main                                                          |

An agent that stops early sets `ready-for-human` and leaves a Comment (see `ticket-template.md`, Failure handling).
