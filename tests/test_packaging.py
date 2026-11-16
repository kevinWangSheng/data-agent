"""An installed agent (a wheel, as Harbor installs it) must find its Skills without the checkout,
and `data-agent web` from a wheel serves the built web UI, so its user needs no Node."""

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_agent.agent import QUERY_SKILLS
from data_agent.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
# Talk to the local server directly, whatever proxy the environment names.
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def build_wheel(out: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    uv = shutil.which("uv")
    assert uv is not None
    return subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def extract_wheel(tmp_path: Path, out: Path) -> Path:
    (wheel,) = out.glob("*.whl")
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    return site


def build_and_extract_wheel(tmp_path: Path, env: dict[str, str] | None = None) -> Path:
    proc = build_wheel(tmp_path / "dist", env)
    assert proc.returncode == 0, proc.stderr
    return extract_wheel(tmp_path, tmp_path / "dist")


def run_installed(tmp_path: Path, site: Path, probe: str) -> subprocess.CompletedProcess[str]:
    """The extracted package shadows the editable checkout; dependencies come from the venv."""
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=os.environ | {"PYTHONPATH": str(site)},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_installed_wheel_loads_its_skills(tmp_path: Path) -> None:
    site = build_and_extract_wheel(tmp_path)
    # The extracted package shadows the editable checkout; dependencies still come from the venv.
    probe = (
        "from data_agent import agent; "
        "print(agent.SKILLS_DIR); "
        f"assert agent.load_skills({QUERY_SKILLS!r}, agent.SKILLS_DIR)"
    )
    proc = run_installed(tmp_path, site, probe)
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()).is_relative_to(site)


def test_installed_wheel_loads_every_run_config(tmp_path: Path) -> None:
    site = build_and_extract_wheel(tmp_path)
    probe = (
        "from data_agent.run_config import CONFIG_NAMES, load_config; "
        "configs = [load_config(name) for name in CONFIG_NAMES]; "
        "assert len(configs) == 6, CONFIG_NAMES; "
        "print(*{c.skills_dir for c in configs}, sep='\\n')"
    )
    proc = run_installed(tmp_path, site, probe)
    assert proc.returncode == 0, proc.stderr
    assert all(Path(line).is_relative_to(site) for line in proc.stdout.splitlines())


def test_installed_wheel_reports_the_commit_it_was_built_from(tmp_path: Path) -> None:
    """Away from the checkout there is no git to ask, so the commit is written in at build time."""
    site = build_and_extract_wheel(tmp_path)
    proc = run_installed(tmp_path, site, "from data_agent import agent; print(agent.git_commit())")
    assert proc.returncode == 0, proc.stderr
    built_from = subprocess.run(
        ["git", "describe", "--always", "--dirty", "--abbrev=40"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert proc.stdout.strip() == built_from


def test_wheel_builds_without_git_and_reports_an_unknown_commit(tmp_path: Path) -> None:
    """An environment without git (a slim image) still builds the wheel."""
    no_git = tmp_path / "bin"
    no_git.mkdir()
    site = build_and_extract_wheel(tmp_path, env=os.environ | {"PATH": str(no_git)})
    assert (site / "data_agent" / "BUILD_COMMIT").read_text() == "unknown"


def test_installed_wheel_loads_the_verifier_agents(tmp_path: Path) -> None:
    site = build_and_extract_wheel(tmp_path)
    probe = (
        "from data_agent import verifiers; "
        "agents = verifiers.verifier_agents('data'); "
        "assert set(agents) == {'verifier'}; "
        "print(verifiers.AGENTS_DIR)"
    )
    proc = run_installed(tmp_path, site, probe)
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()).is_relative_to(site)


def test_runtime_requirements_include_pytz() -> None:
    """DuckDB's Python client needs `pytz` to hand back a TIMESTAMPTZ value. Locally it rides in
    on the dev group (dbt), so only the no-dev export Harbor installs from shows it missing."""
    uv = shutil.which("uv")
    assert uv is not None
    proc = subprocess.run(
        [uv, "export", "--frozen", "--no-dev", "--no-emit-project"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    names = {line.split("==")[0].strip() for line in proc.stdout.splitlines() if "==" in line}
    assert "pytz" in names


# --- The web UI ----------------------------------------------------------------------------------


def no_runs(args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
    raise AssertionError("these tests start no run")


@pytest.fixture
def ui_dir(tmp_path: Path) -> Path:
    """A built UI as Vite lays it out: index.html at the top, the bundle under assets/."""
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text('<div id="root"></div><script src="/assets/app.js"></script>')
    (ui / "assets" / "app.js").write_text("console.log('app')")
    (tmp_path / "secret.txt").write_text("outside the UI")
    return ui


def ui_client(tmp_path: Path, ui_dir: Path) -> TestClient:
    runs = tmp_path / "runs"
    runs.mkdir()
    return TestClient(create_app(runs, [], no_runs, ui_dir=ui_dir))


def test_serves_the_built_ui(tmp_path: Path, ui_dir: Path) -> None:
    client = ui_client(tmp_path, ui_dir)
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in page.text
    script = client.get("/assets/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]


def test_every_page_address_gets_the_app(tmp_path: Path, ui_dir: Path) -> None:
    """The app routes in the browser, so a reload of `/runs/<id>` must get index.html too."""
    client = ui_client(tmp_path, ui_dir)
    for path in ("/runs/20261116T090000Z-abc123", "/runs/x?step=s1&filter=sql"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert '<div id="root">' in page.text


def test_unknown_api_and_asset_paths_are_not_the_app(tmp_path: Path, ui_dir: Path) -> None:
    client = ui_client(tmp_path, ui_dir)
    assert client.get("/api/nope").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/api/runs").json() == []


def test_serves_nothing_outside_the_ui(tmp_path: Path, ui_dir: Path) -> None:
    client = ui_client(tmp_path, ui_dir)
    for path in ("/assets/../../secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt", "/../secret.txt"):
        assert "outside the UI" not in client.get(path).text, path


def test_without_a_built_ui_the_page_says_how_to_build_it(tmp_path: Path) -> None:
    client = ui_client(tmp_path, tmp_path / "not-built")
    page = client.get("/")
    assert page.status_code == 503
    assert "pnpm" in page.text
    # The API works without the UI.
    assert client.get("/api/options").status_code == 200


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fetch(url: str) -> tuple[int, str, str]:
    try:
        with NO_PROXY.open(url, timeout=2) as response:
            return response.status, response.headers["content-type"], response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers["content-type"], exc.read().decode()


def test_wheel_built_with_the_ui_turned_off_never_calls_pnpm(tmp_path: Path) -> None:
    """An evaluation builds the agent's wheel with DATA_AGENT_WHEEL_UI=0: no frontend build, so a
    pinned checkout needs no pnpm, and a broken frontend build cannot fail the run."""
    fake = tmp_path / "fake-pnpm"
    fake.mkdir()
    pnpm = fake / "pnpm"
    pnpm.write_text("#!/bin/sh\necho 'pnpm was called' >&2\nexit 1\n")
    pnpm.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "DATA_AGENT_WHEEL_UI"}
    env["PATH"] = f"{fake}{os.pathsep}{env['PATH']}"
    # The fake is what the build hook finds: left on, the UI build calls it and the wheel fails.
    on = build_wheel(tmp_path / "on", env)
    assert on.returncode != 0
    assert "pnpm was called" in on.stderr
    off = build_wheel(tmp_path / "off", env | {"DATA_AGENT_WHEEL_UI": "0"})
    assert off.returncode == 0, off.stderr
    assert "pnpm" not in off.stderr  # neither called nor warned about: skipping is on purpose
    site = extract_wheel(tmp_path, tmp_path / "off")
    assert not (site / "data_agent" / "web" / "static").exists()
    assert (site / "data_agent" / "BUILD_COMMIT").is_file()


def test_data_agent_web_from_the_wheel_serves_the_ui(tmp_path: Path) -> None:
    """Built where pnpm is on PATH (a checkout, CI): the wheel carries the built UI."""
    site = build_and_extract_wheel(tmp_path)
    assert (site / "data_agent" / "web" / "static" / "index.html").is_file()
    runs = tmp_path / "runs"
    runs.mkdir()
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "data_agent", "web", "--runs-dir", str(runs), "--port", str(port)],
        cwd=tmp_path,
        env=os.environ | {"PYTHONPATH": str(site)},
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            assert server.poll() is None, "server exited"
            try:
                status, _, page = fetch(f"{base}/runs/some-run")
                break
            except OSError:
                assert time.monotonic() < deadline, "server never answered"
                time.sleep(0.2)
        assert status == 200
        assert '<div id="root">' in page
        (src,) = re.findall(r'<script[^>]+src="([^"]+)"', page)
        status, content_type, bundle = fetch(f"{base}{src}")
        assert status == 200, src
        assert "javascript" in content_type
        # The local build follows runs from the backend; the static site's reader is left out.
        assert "new EventSource" in bundle
        assert "index.json" not in bundle
    finally:
        server.terminate()
        server.wait(timeout=60)
