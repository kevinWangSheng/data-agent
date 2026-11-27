"""Spike probe for ticket 152, run inside the data-agent container by probe.sh (never on the host).

It calls data-agent's own code with no agent session and no model: DockerSandbox and
execute_tool (sandbox_exec), the read-only gateways, DictionaryBuild (the value dictionary) and
DbtExecutor (dbt_execute). It prints a yes/no and evidence for each of the ticket's four
questions. Every sandbox container it starts is named `da-spike-152-<tag>-sandbox-<case>`, removed
by run id (remove_run_containers) at the end; it never calls remove_orphaned_containers, which
would remove other runs' containers on the shared daemon (see the research note).

Environment (set by probe.sh): SPIKE_APP (the staged code), SPIKE_PREFIX
(`da-spike-152-<tag>`), SPIKE_SAME (a host directory mounted at the same path), SPIKE_DIFF
(a host directory mounted at another path), SPIKE_VOL (a named volume's mount point),
SPIKE_VOLUME (its name), SPIKE_PG_HOST, DA_SPIKE_PG_URL, SPIKE_BAKED (a tmpfs of the
data-agent container), and canary variables whose values must never reach a sandbox
(SPIKE_CANARIES lists their names).
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import traceback
from pathlib import Path

import duckdb
import psycopg

from data_agent import sandbox
from data_agent.context_tools import DictionaryBuild
from data_agent.dbt_execute import DbtExecutor
from data_agent.gateway import DuckDBGateway, PostgresGateway, QueryBlocked
from data_agent.sandbox import DockerSandbox, QueryResults, SandboxError, execute_tool

# The ticket's name prefix for everything the spike starts: the run id carries the invocation's
# prefix (its sandboxes' run label is unique, and so is the name container_name makes from it).
PREFIX = os.environ["SPIKE_PREFIX"]
sandbox.NAME_PREFIX = ""

APP = Path(os.environ["SPIKE_APP"])
SAME = Path(os.environ["SPIKE_SAME"])
DIFF = Path(os.environ["SPIKE_DIFF"])
VOL = Path(os.environ["SPIKE_VOL"])
VOLUME = os.environ["SPIKE_VOLUME"]
BAKED = Path(os.environ["SPIKE_BAKED"])
PG_HOST = os.environ["SPIKE_PG_HOST"]
PG_ENV = "DA_SPIKE_PG_URL"
CANARIES = os.environ["SPIKE_CANARIES"].split(",")
SQL = (APP / "tests/fixtures/shop.sql").read_text()
TIER_SQL = "SELECT tier, count(*) AS n FROM raw.customers GROUP BY tier ORDER BY tier"
WRITE_SQL = "DELETE FROM raw.customers"
# A name, never a literal address (the pre-commit scan refuses public IPs).
EXTERNAL_HOST = "pypi.org"

answers: dict[str, bool] = {}
not_run: list[str] = []
run_ids: list[str] = []


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)


def show(label: str, text: object) -> None:
    body = text if isinstance(text, str) else json.dumps(text, indent=2, default=str)
    print(f"--- {label}\n{textwrap.indent(body.rstrip(), '    ')}", flush=True)


def answer(question: str, ok: bool, why: str) -> None:
    answers[question] = ok
    print(f">>> {question}: {'YES' if ok else 'NO'} — {why}", flush=True)


def docker(*args: str) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    return (done.stdout + done.stderr).strip()


def inspect(name: str) -> dict[str, object]:
    data = json.loads(docker("inspect", name))[0]
    return {
        "Id": data["Id"][:12],
        "Name": data["Name"],
        "Config.User": data["Config"]["User"],
        "Config.Env": data["Config"]["Env"],
        "Config.Labels": data["Config"]["Labels"],
        "HostConfig.NetworkMode": data["HostConfig"]["NetworkMode"],
        "HostConfig.NanoCpus": data["HostConfig"]["NanoCpus"],
        "HostConfig.Memory": data["HostConfig"]["Memory"],
        "Mounts": [
            {k: m.get(k) for k in ("Type", "Source", "Destination", "RW")} for m in data["Mounts"]
        ],
    }


def new_sandbox(case: str, workspace: Path, skills: Path) -> DockerSandbox:
    run_id = f"{PREFIX}-sandbox-{case}"
    run_ids.append(run_id)
    return DockerSandbox(workspace=workspace, skills_dir=skills, run_id=run_id)


# Passed as `python3 -c '...'`: the host names in double quotes (json.dumps), never single.
NET_PROBE = f"""
import socket
for host, port in (({json.dumps(PG_HOST)}, 5432), ({json.dumps(EXTERNAL_HOST)}, 443)):
    try:
        socket.create_connection((host, port), timeout=3).close()
        print(f"net {{host}}:{{port}} REACHABLE")
    except OSError as exc:
        print(f"net {{host}}:{{port}} unreachable ({{type(exc).__name__}}: {{exc}})")
"""


def net_probe_here() -> str:
    """The same probe from the data-agent container: the positive control."""
    lines = []
    for host, port in ((PG_HOST, 5432), (EXTERNAL_HOST, 443)):
        try:
            socket.create_connection((host, port), timeout=3).close()
            lines.append(f"net {host}:{port} REACHABLE")
        except OSError as exc:
            lines.append(f"net {host}:{port} unreachable ({type(exc).__name__}: {exc})")
    return "\n".join(lines)


def setup() -> tuple[Path, Path, Path]:
    section("Setup (inside the data-agent container)")
    show(
        "data-agent process",
        {
            "uid": os.getuid(),
            "gid": os.getgid(),
            "groups": os.getgroups(),
            "pid (OWNER_LABEL value)": os.getpid(),
            "docker version": docker(
                "version", "--format", "{{.Client.Version}} / {{.Server.Version}}"
            ),
            "/proc/1/mountinfo (spike mounts)": [
                " ".join(line.split()[3:5])
                for line in Path("/proc/self/mountinfo").read_text().splitlines()
                if any(str(p) in line for p in (SAME, DIFF, VOL, "/var/run/docker.sock"))
            ],
        },
    )
    skills = SAME / "skills"
    shutil.copytree(APP / "skills", skills, dirs_exist_ok=True)
    data = SAME / "data"
    data.mkdir(exist_ok=True)
    db = data / "shop.duckdb"
    db.unlink(missing_ok=True)
    con = duckdb.connect(str(db))
    con.execute(SQL)
    con.close()
    with psycopg.connect(os.environ[PG_ENV], autocommit=True) as pg:
        pg.execute(SQL)  # pyright: ignore[reportArgumentType]
    show("prepared", {"skills": str(skills), "duckdb": str(db), "postgres": PG_HOST})
    return skills, db, data


def q1_q2_same_path(skills: Path, db: Path) -> None:
    section("Q1 + Q2a: DockerSandbox / sandbox_exec, workspace and skills at the same path")
    run = SAME / "run-same"
    workspace = run / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    gateway = DuckDBGateway(db)
    results = QueryResults()
    result_id = results.add(gateway.query(TIER_SQL))
    box = new_sandbox("same", workspace, skills)
    command = textwrap.dedent(
        f"""\
        echo "## id"; id
        echo "## pwd"; pwd
        echo "## data file written by the agent side"; cat data/{result_id}.csv
        echo "## skills (read-only?)"; ls /skills | head -3
        touch /skills/x 2>&1 && echo "skills WRITABLE" || echo "skills read-only"
        echo "## write outputs/"; mkdir -p outputs && echo from-sandbox > outputs/report.txt
        echo "## env"; env | sort
        echo "## /var/run/docker.sock"
        test -e /var/run/docker.sock && echo "socket PRESENT" || echo "no socket"
        echo "## /workspace/.. (the run dir with profiles/ is not here)"; ls -a /workspace/..
        echo "## network"; python3 -c '{NET_PROBE}'
        """
    )
    try:
        reply = execute_tool(box, results, command, 60, [result_id])
    except SandboxError as exc:
        show("execute_tool raised", str(exc))
        answer("Q1", False, "the sandbox did not start or run from inside the container")
        answer("Q2", False, "same-path mount failed")
        return
    show("sandbox_exec reply", reply)
    assert box.container is not None
    facts = inspect(box.container)
    show("docker inspect (sandbox)", facts)
    seen_host = (workspace / "outputs/report.txt").read_text().strip()
    show("outputs/report.txt as the data-agent container reads it", seen_host)
    ok1 = "exit_code: 0" in reply
    answer("Q1", ok1, f"container {facts['Id']} started via the mounted socket; exit_code 0")
    ok2 = (
        ok1
        and "C0001" not in reply  # the CSV is the grouped result, not raw rows
        and "tier,n" in reply
        and "outputs/report.txt" in reply
        and seen_host == "from-sandbox"
        and "skills read-only" in reply
    )
    answer(
        "Q2 (same-path bind)",
        ok2,
        "data_files CSV visible in the sandbox, its outputs/ file visible to the agent side and "
        "listed in output_files, skills mounted read-only",
    )
    env_section = reply.split("## env", 1)[1].split("## /var/run", 1)[0]
    # The entries of /workspace/.., one per line after the section's header line.
    parent = reply.split("## /workspace/..", 1)[1].split("## network", 1)[0]
    parent_listing = [line.strip() for line in parent.splitlines()[1:]]
    leaked = [n for n in CANARIES if n in env_section or os.environ[n] in reply]
    mounts = facts["Mounts"]
    assert isinstance(mounts, list)
    net_none = facts["HostConfig.NetworkMode"] == "none"
    no_reach = "REACHABLE" not in reply.split("## network", 1)[1]
    ok3 = (
        not leaked
        and len(mounts) == 2
        and net_none
        and no_reach
        and "no socket" in reply
        and "profiles" not in parent_listing
    )
    control = net_probe_here()
    show("network probe from the data-agent container (control)", control)
    show(
        "canaries in the data-agent container (names only)",
        {n: "set" if os.environ.get(n) else "unset" for n in CANARIES},
    )
    answer(
        "Q3",
        ok3,
        f"leaked canaries: {leaked or 'none'}; mounts: {len(mounts)} (workspace, skills); "
        f"NetworkMode={facts['HostConfig.NetworkMode']}; no socket; control reaches "
        f"postgres: {'REACHABLE' in control.splitlines()[0]}",
    )


def q2_alternatives(skills: Path) -> None:
    section("Q2b: the other path layouts")
    # Skills that exist only in the data-agent container (as if baked into its image): BAKED is
    # a tmpfs of that container, so the daemon has no such path.
    baked = BAKED / "skills"
    shutil.copytree(APP / "skills", baked, dirs_exist_ok=True)
    cases = [
        ("diff", DIFF / "workspace", skills, "workspace at a container path ≠ host path"),
        ("vol", VOL / "workspace", skills, "workspace on a named volume (bind source = its path)"),
        ("baked", SAME / "run-baked" / "workspace", baked, "skills only in this container's fs"),
    ]
    for case, workspace, skill_dir, label in cases:
        try:
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "marker.txt").write_text(f"{case}\n")
        except OSError as exc:
            # The named volume lives on the Docker VM's disk (full on colima, 2026-11-27).
            show(f"{case}: {label}", f"NOT RUN: could not write the workspace ({exc})")
            not_run.append(f"Q2 ({case})")
            continue
        box = new_sandbox(case, workspace, skill_dir)
        try:
            done = box.execute("cat marker.txt; ls /skills | head -2", 30)
            ok = done.exit_code == 0 and done.stdout.splitlines()[:1] == [case]
            outcome = f"started; exit {done.exit_code}; stdout {done.stdout.strip()!r}"
        except SandboxError as exc:
            ok, outcome = False, f"SandboxError: {exc}"
        show(f"{case}: {label}", outcome)
        answer(f"Q2 ({case})", ok, outcome.splitlines()[0][:200])
    if "Q2 (vol)" in not_run:
        not_run.append("Q2 (volume-subpath)")
        return
    # A named volume does work when the mount names the volume, not a path: the change
    # DockerSandbox.start would need (`type=volume,...,volume-subpath=`, Docker API ≥ 1.45).
    out = docker(
        "run", "--rm", "--name", f"{PREFIX}-volume-subpath", "--network", "none",
        "--mount", f"type=volume,source={VOLUME},volume-subpath=workspace,target=/workspace",
        "alpine", "cat", "/workspace/marker.txt",
    )  # fmt: skip
    show("vol via type=volume,volume-subpath (raw docker run, not DockerSandbox)", out)
    answer(
        "Q2 (volume-subpath, needs a src change)",
        out == "vol",
        "the volume's subdirectory is the workspace when the mount names the volume",
    )


def q4(db: Path) -> None:
    section("Q4: gateway, value dictionary and dbt_execute in the data-agent container")
    ok = True
    gateways = {"duckdb": DuckDBGateway(db), "postgres": PostgresGateway(os.environ[PG_ENV])}
    for name, gateway in gateways.items():
        rows = gateway.query(TIER_SQL)
        show(f"{name} gateway: {TIER_SQL}", {"columns": rows.columns, "rows": rows.rows})
        try:
            gateway.query(WRITE_SQL)
            blocked = "NOT blocked"
            ok = False
        except QueryBlocked as exc:
            blocked = f"QueryBlocked: {exc}"
        show(f"{name} gateway: {WRITE_SQL}", blocked)
        dictionary = DictionaryBuild(gateway).run()
        search = dictionary.search("gold")
        show(f"{name} value dictionary: search('gold')", search)
        ok = ok and bool(rows.rows) and dictionary.coverage.error is None and "Gold" in search
    for name, kwargs in (
        ("duckdb", {"duckdb": db}),
        ("postgres", {"postgres_env": PG_ENV}),
    ):
        run = SAME / f"run-dbt-{name}"
        workspace = run / "workspace"
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.copytree(APP / "tests/fixtures/dbt_shop", workspace / "dbt_shop")
        executor = DbtExecutor(
            workspace=workspace,
            target="scratch",
            run_id=f"spike152{name}",
            profiles_dir=run / "profiles",
            **kwargs,  # pyright: ignore[reportArgumentType]
        )
        reply = executor.execute("run")
        show(f"dbt_execute run ({name}, scratch)", reply)
        ok = ok and "exit_code: 0" in reply
    answer(
        "Q4", ok, "both gateways read and block writes; dictionary built; dbt run exit 0 on both"
    )


def main() -> int:
    try:
        skills, db, _ = setup()
        q1_q2_same_path(skills, db)
        q2_alternatives(skills)
        q4(db)
    except Exception:
        traceback.print_exc()
        answers["probe"] = False
    finally:
        section("Cleanup (the probe's own sandbox containers, by run id)")
        for run_id in run_ids:
            try:
                sandbox.remove_run_containers(run_id)
                print(f"removed the containers of run {run_id}")
            except SandboxError as exc:
                print(f"could not remove run {run_id}: {exc}")
    section("Answers")
    for question, ok in answers.items():
        print(f"{question}: {'YES' if ok else 'NO'}")
    expected_no = {"Q2 (diff)", "Q2 (vol)", "Q2 (baked)"}
    unexpected = [q for q, ok in answers.items() if ok == (q in expected_no)]
    for question in not_run:
        print(f"{question}: NOT RUN")
    print("all as expected" if not unexpected else f"UNEXPECTED: {unexpected}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
