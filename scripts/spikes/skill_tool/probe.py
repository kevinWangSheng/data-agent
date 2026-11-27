"""Ticket 142 spike: the SDK's Skill tool, a local plugin's Skills, and a Skill script's path in
the sandbox. One command answers the ticket's three questions and prints the evidence.

    uv run python scripts/spikes/skill_tool/probe.py                      # stub endpoint, $0
    uv run python scripts/spikes/skill_tool/probe.py --endpoint model     # DATA_AGENT_* model

`--endpoint stub` (the default) puts a scripted Anthropic endpoint on localhost: it calls the Skill
tool as a model would and records every request, so the CLI's side (the listing the model is sent,
the Skill tool, the text it loads) is seen without a model. `--endpoint model` sends the same
sessions to the model `DATA_AGENT_*` names (`ModelSettings.from_env`); only that answers whether
the model itself finds and follows the Skill. `--sandbox docker` (the default; `none` skips it)
runs the Skill's script the way sandbox_exec does, under the status quo and both candidates.

The options mirror `build_options` (agent.py): its built-in tools, `dontAsk`, user settings only,
strict MCP config, a custom system prompt; plus what this spike adds (the Skill tool, `skills`,
`plugins`). Only the probe plugin's entries are printed: the user's own Skills and plugins stay out.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import SdkPluginConfig

from data_agent.agent import BUILTIN_TOOLS, DENIED_TOOLS, ModelSettings
from data_agent.sandbox import (
    CPUS,
    MEMORY,
    WORKDIR,
    DockerSandbox,
    LocalSandbox,
    QueryResults,
    Sandbox,
    SandboxError,
    container_name,
    execute_tool,
    remove,
    run_docker,
)

HERE = Path(__file__).resolve().parent
PLUGIN_DIR = HERE / "plugin"
MANIFEST = Path(".claude-plugin") / "plugin.json"
# The repo's runs/ (not in git): Docker on this Mac (colima) shares only the home directory.
RUNS_DIR = HERE.parents[2] / "runs" / "spike-142"

PROBE_SKILL = "skill-probe"
HIDDEN_SKILL = "hidden-probe"
# In SKILL.md bodies only, never a description: seen only once the Skill tool loads the body.
CANARY = "CANARY-142-plum-orbit"
HIDDEN_CANARY = "CANARY-142-hidden"
SCRIPT_MARK = "WHERE-142 ran"
SKILL_TOOL = "Skill"
# The first line of the text the CLI injects after a Skill tool call (CLI 2.1.330).
BASE_DIR_LINE = "Base directory for this skill: "
LISTING_HEAD = "The following skills are available for use with the Skill tool:"

SYSTEM_PROMPT = "You are a probe for a test of the Skill tool. Follow the user's steps exactly."
USER_PROMPT = """\
1. Call the Skill tool with skill "{probe}" and do what the Skill says.
2. Then call the Skill tool once with skill "{hidden}", and add a fourth line to your reply:
HIDDEN: <the Skill tool's reply to that call, in one line>
Do not call any other tool."""
SESSION_TIMEOUT_S = 240


def plugin_name(plugin_dir: Path) -> str:
    """The plugin's name from its manifest: the prefix of every Skill it holds."""
    manifest = json.loads((plugin_dir / MANIFEST).read_text())
    name = manifest.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError(f"{plugin_dir / MANIFEST}: name must be kebab-case, got {name!r}")
    return name


def plugin_skills(plugin_dir: Path) -> list[str]:
    """`<plugin>:<skill>` for every `skills/<skill>/SKILL.md` in the plugin."""
    name = plugin_name(plugin_dir)
    return sorted(f"{name}:{p.parent.name}" for p in (plugin_dir / "skills").glob("*/SKILL.md"))


def linked_plugin(dest: Path, source: Path) -> Path:
    """A plugin at `dest` whose Skills are symlinks to `source`'s, as the spec packages each
    `[skills].dirs` entry into the run's temp directory: the manifest copied, each Skill linked."""
    (dest / MANIFEST).parent.mkdir(parents=True)
    shutil.copyfile(source / MANIFEST, dest / MANIFEST)
    (dest / "skills").mkdir()
    for skill in sorted((source / "skills").glob("*/SKILL.md")):
        (dest / "skills" / skill.parent.name).symlink_to(skill.parent.resolve())
    return dest


def probe_options(
    settings: ModelSettings,
    plugin_dirs: Sequence[Path],
    skills: list[str],
    cwd: Path,
    config_dir: Path | None = None,
    stderr: Callable[[str], None] | None = None,
) -> ClaudeAgentOptions:
    """`build_options`' shape plus the Skill tool. `tools` must name the Skill tool: `skills`
    adds only `Skill(<name>)` allow rules, and `--tools` without it leaves the tool out of the
    session. `config_dir` is a conversation's own `CLAUDE_CONFIG_DIR` (`ConversationTurn`)."""
    tools = [*BUILTIN_TOOLS, SKILL_TOOL]
    plugins: list[SdkPluginConfig] = [{"type": "local", "path": str(p)} for p in plugin_dirs]
    session_env = {} if config_dir is None else {"CLAUDE_CONFIG_DIR": str(config_dir)}
    return ClaudeAgentOptions(
        model=settings.model,
        env=settings.sdk_env() | session_env,
        system_prompt=SYSTEM_PROMPT,
        cwd=str(cwd),
        tools=tools,
        allowed_tools=list(tools),
        disallowed_tools=list(DENIED_TOOLS),
        permission_mode="dontAsk",
        mcp_servers={},
        strict_mcp_config=True,
        setting_sources=["user"],
        skills=skills,
        plugins=plugins,
        max_turns=6,
        stderr=stderr,
    )


# --- The stub endpoint -----------------------------------------------------------------------


def tool_results(body: dict[str, Any]) -> int:
    """How many tool results the request's messages hold: the stub's step in its script."""
    return sum(
        1
        for message in body.get("messages", [])
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    )


def scripted_reply(body: dict[str, Any], probe: str, hidden: str) -> tuple[list[dict], str]:
    """The stub model's turn: call the Skill tool for `probe`, then for `hidden`, then answer."""
    step = tool_results(body)
    if step < 2:
        skill = probe if step == 0 else hidden
        call = {"type": "tool_use", "id": f"toolu_{step}", "name": SKILL_TOOL, "input": {}}
        return [call | {"input": {"skill": skill}}], "tool_use"
    return [{"type": "text", "text": "stub answer"}], "end_turn"


def sse_events(model: str, blocks: list[dict], stop: str) -> Iterator[tuple[str, dict]]:
    """`blocks` as the Messages API streams them."""
    usage = {"input_tokens": 1, "output_tokens": 1}
    yield (
        "message_start",
        {
            "type": "message_start",
            "message": {"id": "msg_stub", "type": "message", "role": "assistant", "model": model}
            | {"content": [], "stop_reason": None, "usage": usage},
        },
    )
    for i, block in enumerate(blocks):
        if block["type"] == "text":
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
        else:
            start = block | {"input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        yield (
            "content_block_start",
            {"type": "content_block_start", "index": i} | {"content_block": start},
        )
        yield "content_block_delta", {"type": "content_block_delta", "index": i, "delta": delta}
        yield "content_block_stop", {"type": "content_block_stop", "index": i}
    yield (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop},
            "usage": {"output_tokens": 1},
        },
    )
    yield "message_stop", {"type": "message_stop"}


@dataclass
class StubEndpoint:
    probe: str
    hidden: str
    requests: list[dict[str, Any]] = field(default_factory=list)
    url: str = ""


@contextmanager
def serve_stub(stub: StubEndpoint) -> Iterator[StubEndpoint]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            stub.requests.append({"path": self.path, "body": body})
            blocks, stop = scripted_reply(body, stub.probe, stub.hidden)
            model = str(body.get("model"))
            self.send_response(200)
            if not body.get("stream"):
                data = json.dumps(
                    {"id": "msg_stub", "type": "message", "role": "assistant", "model": model}
                    | {"content": blocks, "stop_reason": stop}
                    | {"usage": {"input_tokens": 1, "output_tokens": 1}}
                ).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event, data in sse_events(model, blocks, stop):
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stub.url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()


def sent_listing(requests: Sequence[dict[str, Any]]) -> list[str]:
    """The Skill names the CLI listed to the model in its first request (a system reminder in
    the first user message): `- <name>: <description>` lines after `LISTING_HEAD`."""
    if not requests:
        return []
    texts = [text_of(m.get("content")) for m in requests[0]["body"].get("messages", [])]
    text = next((t for t in texts if LISTING_HEAD in t), "")
    if not text:
        return []
    block = text.split(LISTING_HEAD, 1)[1].lstrip("\n").split("\n\n", 1)[0]
    return [m.group(1) for m in re.finditer(r"^- (\S+?):\s", block, re.MULTILINE)]


# --- A session and what it showed ------------------------------------------------------------


@dataclass
class SkillCall:
    skill: str
    result: str
    is_error: bool


@dataclass
class Session:
    label: str
    init: dict[str, Any] = field(default_factory=dict)
    calls: list[SkillCall] = field(default_factory=list)
    loaded: list[str] = field(default_factory=list)  # the Skill texts the CLI injected
    answer: str = ""
    cost_usd: float | None = None
    error: str | None = None
    listing: list[str] = field(default_factory=list)  # stub only: what the model was sent

    def probe_init(self, plugin: str) -> dict[str, Any]:
        """The init message's entries for the probe plugin only."""
        prefix = f"{plugin}:"
        return {
            "tools_has_Skill": SKILL_TOOL in self.init.get("tools", []),
            "skills": [s for s in self.init.get("skills", []) if s.startswith(prefix)],
            "plugins": [p for p in self.init.get("plugins", []) if p.get("name") == plugin],
            "plugin_errors": self.init.get("plugin_errors", []),
        }


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", part)) for part in content if isinstance(part, dict))
    return "" if content is None else str(content)


async def run_session(label: str, options: ClaudeAgentOptions, prompt: str) -> Session:
    session = Session(label)
    names: dict[str, str] = {}
    async with ClaudeSDKClient(options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                session.init = message.data
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock) and block.name == SKILL_TOOL:
                        names[block.id] = str(block.input.get("skill"))
                    elif isinstance(block, TextBlock):
                        session.answer += block.text
            elif isinstance(message, UserMessage):
                blocks = message.content if isinstance(message.content, list) else []
                for block in blocks:
                    if isinstance(block, ToolResultBlock) and block.tool_use_id in names:
                        session.calls.append(
                            SkillCall(
                                names[block.tool_use_id],
                                text_of(block.content),
                                bool(block.is_error),
                            )
                        )
                    elif isinstance(block, TextBlock) and block.text.startswith(BASE_DIR_LINE):
                        session.loaded.append(block.text)
                if isinstance(message.content, str) and message.content.startswith(BASE_DIR_LINE):
                    session.loaded.append(message.content)
            elif isinstance(message, ResultMessage):
                session.cost_usd = message.total_cost_usd
                if message.is_error:
                    session.error = f"{message.subtype}: {message.result}"
    return session


def skill_dir_in(text: str) -> str | None:
    """The value the CLI gave `${CLAUDE_SKILL_DIR}`: the probe Skill's `SKILL_DIR:` line."""
    match = re.search(r"^SKILL_DIR: (.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


# --- Question 3: the Skill's script in the sandbox ------------------------------------------


@dataclass
class SamePathDockerSandbox(DockerSandbox):
    """Candidate A: `DockerSandbox` plus each Skill directory mounted read-only at its host path
    (`paths`: the path the CLI fills in -> the directory it names), so `${CLAUDE_SKILL_DIR}` as the
    SDK fills it is also a path in the container. Its `docker run` mirrors `DockerSandbox.start`."""

    paths: dict[str, Path] = field(default_factory=dict)

    def start(self) -> str:
        name = container_name(self.run_id)
        same_path = [
            arg
            for target, source in self.paths.items()
            for arg in ("--mount", f"type=bind,source={source.resolve()},target={target},readonly")
        ]
        argv = [
            *("run", "-d", "--init", "--pull", "never", "--name", name),
            *("--user", f"{os.getuid()}:{os.getgid()}", "--network", "none"),
            *("--cpus", CPUS, "--memory", MEMORY),
            *("--mount", f"type=bind,source={self.workspace.resolve()},target={WORKDIR}"),
            *("--mount", f"type=bind,source={self.skills_dir},target=/skills,readonly"),
            *same_path,
            *("-w", WORKDIR, self.image, "sleep", "infinity"),
        ]
        done = run_docker(argv)
        if done.exit_code != 0:
            raise SandboxError(f"could not start the sandbox container: {done.stderr.strip()}")
        return name


@dataclass
class SandboxTrial:
    label: str
    command: str
    reached: bool
    reply: str


def sandbox_exec(sandbox: Sandbox, command: str) -> tuple[bool, str]:
    """`command` as sandbox_exec runs it (`execute_tool`); whether the script ran, and the reply."""
    try:
        reply = execute_tool(sandbox, QueryResults(), command, 60, [])
    except SandboxError as exc:
        return False, f"SandboxError: {exc}"
    return SCRIPT_MARK in reply, reply.strip()


def sandbox_trials(skill_dirs: Sequence[str], backend: str) -> list[SandboxTrial]:
    """The probe Skill's script, named as `${CLAUDE_SKILL_DIR}` gave it (each distinct value in
    `skill_dirs`) and as candidate B would (the mount path). The local backend always; with
    `docker` also the status-quo container and candidate A's, which mounts every filled path."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="workspace-", dir=RUNS_DIR))
    skills_dir = PLUGIN_DIR / "skills"
    sdk = [(d, f'python3 "{d}/where.py"') for d in dict.fromkeys(skill_dirs)]
    mount = f'python3 "/skills/{PROBE_SKILL}/where.py"'
    local = LocalSandbox(workspace, skills_dir)
    run_id = f"spike-142-{uuid.uuid4().hex[:8]}"
    status_quo = DockerSandbox(workspace, skills_dir, f"{run_id}-a")
    same_path = SamePathDockerSandbox(
        workspace, skills_dir, f"{run_id}-b", paths={d: Path(d) for d, _ in sdk}
    )
    runs: list[tuple[str, Sandbox, str]] = [
        *((f"local, SDK path {d}", local, command) for d, command in sdk),
        ("local, mount path (B)", local, mount),
    ]
    if backend == "docker":
        runs += [
            *((f"docker as today, SDK path {d}", status_quo, command) for d, command in sdk),
            ("docker as today, mount path (B)", status_quo, mount),
            *((f"docker + same-path mount (A), {d}", same_path, command) for d, command in sdk),
        ]
    try:
        return [SandboxTrial(label, cmd, *sandbox_exec(box, cmd)) for label, box, cmd in runs]
    finally:
        if backend == "docker":
            remove([container_name(status_quo.run_id), container_name(same_path.run_id)])
        shutil.rmtree(workspace, ignore_errors=True)


# --- Verdicts --------------------------------------------------------------------------------


def verdict(ok: bool, measured: bool = True) -> str:
    return "未实测" if not measured else ("是" if ok else "否")


def report(sessions: Sequence[Session], trials: Sequence[SandboxTrial], endpoint: str) -> str:
    plugin = plugin_name(PLUGIN_DIR)
    probe, hidden = f"{plugin}:{PROBE_SKILL}", f"{plugin}:{HIDDEN_SKILL}"
    lines: list[str] = [f"endpoint: {endpoint}"]
    for s in sessions:
        lines.append(f"\n## session {s.label}")
        lines.append(f"init (probe plugin only): {json.dumps(s.probe_init(plugin))}")
        if s.listing:
            lines.append(f"Skills listed to the model: {s.listing}")
        for call in s.calls:
            lines.append(f"Skill({call.skill}) -> is_error={call.is_error}: {call.result[:300]!r}")
        for text in s.loaded:
            lines.append("loaded Skill text:\n    " + text.strip().replace("\n", "\n    "))
        if endpoint == "model":
            lines.append(f"answer:\n    {s.answer.strip().replace(chr(10), chr(10) + '    ')}")
            lines.append(f"cost_usd: {s.cost_usd}")
        if s.error:
            lines.append(f"error: {s.error}")

    def loaded_probe(s: Session) -> bool:
        return any(c.skill == probe and not c.is_error for c in s.calls) and any(
            CANARY in t for t in s.loaded
        )

    def hidden_kept_out(s: Session) -> bool:
        listed = hidden not in s.listing if endpoint == "stub" else True
        called = [c for c in s.calls if c.skill == hidden]
        refused = all(c.is_error for c in called) and not any(HIDDEN_CANARY in t for t in s.loaded)
        return listed and refused

    def plugin_found(s: Session) -> bool:
        init = s.probe_init(plugin)
        return bool(init["plugins"]) and not init["plugin_errors"] and probe in init["skills"]

    cli_ok = all(s.probe_init(plugin)["tools_has_Skill"] and loaded_probe(s) for s in sessions)
    model_ok = all(CANARY in s.answer for s in sessions)
    lines.append("\n## verdicts")
    lines.append(
        f"Q1 Skill tool, list, full SKILL.md — CLI side: {verdict(cli_ok)}; "
        f"model follows it ({endpoint}): {verdict(model_ok, endpoint == 'model')}"
    )
    lines.append(
        f"Q2 local plugin found as {probe}, per config dir: "
        + ", ".join(f"{s.label}={verdict(plugin_found(s))}" for s in sessions)
        + "; unlisted Skill kept out: "
        + ", ".join(f"{s.label}={verdict(hidden_kept_out(s))}" for s in sessions)
    )
    dirs = {s.label: skill_dir_in("\n".join(s.loaded)) for s in sessions}
    lines.append(f"Q3 ${{CLAUDE_SKILL_DIR}} as filled: {json.dumps(dirs, ensure_ascii=False)}")
    for t in trials:
        lines.append(f"   sandbox {t.label}: {verdict(t.reached)} — `{t.command}` -> {t.reply!r}")
    return "\n".join(lines)


# --- Entry -----------------------------------------------------------------------------------


async def probe(endpoint: str, backend: str) -> str:
    plugin = plugin_name(PLUGIN_DIR)
    probe_skill, hidden = f"{plugin}:{PROBE_SKILL}", f"{plugin}:{HIDDEN_SKILL}"
    prompt = USER_PROMPT.format(probe=probe_skill, hidden=hidden)
    stub = StubEndpoint(probe_skill, hidden)
    stderr: list[str] = []
    sessions: list[Session] = []
    with tempfile.TemporaryDirectory(prefix="spike-142-") as tmp, serve_stub(stub):
        root = Path(tmp)
        settings = (
            ModelSettings.from_env(os.environ)
            if endpoint == "model"
            else ModelSettings(stub.url, "stub-no-key", "deepseek-flash")
        )
        linked = linked_plugin(root / "linked-plugin", PLUGIN_DIR)
        cases = (
            ("default-config-dir", PLUGIN_DIR, None),
            ("conversation-config-dir", PLUGIN_DIR, root / "conversation" / "claude-config"),
            ("linked-plugin", linked, root / "linked" / "claude-config"),
        )
        for label, plugin_dir, config_dir in cases:
            cwd = root / label / "workspace"
            cwd.mkdir(parents=True)
            if config_dir is not None:
                config_dir.mkdir(parents=True)
            options = probe_options(
                settings, [plugin_dir], [probe_skill], cwd, config_dir, stderr.append
            )
            before = len(stub.requests)
            try:
                session = await asyncio.wait_for(
                    run_session(label, options, prompt), SESSION_TIMEOUT_S
                )
            except Exception as exc:  # a spike reports every failure and goes on
                session = Session(label, error=f"{type(exc).__name__}: {exc}")
            session.listing = sent_listing(stub.requests[before:])
            sessions.append(session)
        filled = [d for s in sessions if (d := skill_dir_in("\n".join(s.loaded)))]
        skill_dirs = filled or [str(PLUGIN_DIR / "skills" / PROBE_SKILL)]
        trials = [] if backend == "none" else sandbox_trials(skill_dirs, backend)
    text = report(sessions, trials, endpoint)
    errors = [line for line in stderr if "error" in line.lower()]
    return text + ("\n\nCLI stderr errors:\n" + "\n".join(errors[-10:]) if errors else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ticket 142 spike: the Skill tool probe.")
    parser.add_argument("--endpoint", choices=("stub", "model"), default="stub")
    parser.add_argument("--sandbox", choices=("docker", "local", "none"), default="docker")
    args = parser.parse_args()
    print(asyncio.run(probe(args.endpoint, args.sandbox)))


if __name__ == "__main__":
    main()
