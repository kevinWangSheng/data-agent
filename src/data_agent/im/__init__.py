"""The IM entry: Slack, Telegram and Discord talk to the agent through the chat backend
(.scratch/im/spec.md).

`data-agent web --im FILE` starts one adapter per platform the file names, in the web process
(`service.start`). An adapter (`adapter.Adapter`) is the common part: it takes messages and button
presses from a platform (`platform.Platform`), runs them through the chat backend's
`Conversations` (`create`, `send`, `cancel`), and follows each turn's event file through the same
`RunsDir` the web UI reads, turning the events into a status message, a clarification, the answer
or a short failure note. The platforms (tickets 131-133) only translate between their library and
`platform`'s types.

The contract of this package was fixed by ticket 130 (human-reviewed) and is held by the
acceptance tests in tests/acceptance/test_im_*.py; ticket 131 implements it. Modules:

- `platform`: the platform interface and the types it carries.
- `adapter`: the common part, and the mapping of platform threads to conversations.
- `config`: reading the `--im` file, and the environment a run gets.
- `format`: splitting an answer, tables as code blocks, the charts note.
- `progress`: the status text of a turn, from its events.
- `service`: what `serve_web` calls to start the adapters.

No module here imports a platform library; those are the optional extra `data-agent[im]`.
"""
