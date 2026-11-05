"""Ticket 52: the v2 tools read the snapshot once per process and re-walk a workspace project's
model files only when one of its model directories changed; workspace file contents are read on
every call."""

import importlib
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest

from data_agent.gateway import DuckDBGateway
from data_agent.verifier_v2 import grain_facts, model_diff
from data_agent.verifier_v2 import project as project_module
from data_agent.verifier_v2.project import model_files, snapshot_documents

# The package exports the function `model_diff` under the module's name.
model_diff_module = importlib.import_module("data_agent.verifier_v2.model_diff")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verifier_v2"
BEFORE, AFTER = FIXTURES / "project_before", FIXTURES / "project_after"


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    """workspace, snapshot and the fixture database, in this test's own directory."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "snap"
    shutil.copytree(AFTER, workspace / "project")
    shutil.copytree(BEFORE, snapshot / "project")
    db = tmp_path / "cmp.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute((FIXTURES / "verifier_v2.sql").read_text())
    finally:
        con.close()
    return workspace, snapshot, db


def reads_under(monkeypatch: pytest.MonkeyPatch, root: Path) -> list[Path]:
    """The files under `root` that the v2 modules read from now on."""
    seen: list[Path] = []
    for module in (project_module, model_diff_module):
        original = module.read_text

        def recording(path: Path, original: Any = original) -> str:
            if path.is_relative_to(root):
                seen.append(path)
            return original(path)

        monkeypatch.setattr(module, "read_text", recording)
    return seen


def test_grain_facts_parses_the_snapshot_yml_once(
    pair: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, snapshot, db = pair
    gateway = DuckDBGateway(db)
    reads = reads_under(monkeypatch, snapshot)
    first = grain_facts(workspace, snapshot, gateway, "stg_orders", ["customer_id"])
    assert any(path.suffix == ".yml" for path in reads)
    reads.clear()
    second = grain_facts(workspace, snapshot, gateway, "stg_orders", ["customer_id"])
    assert reads == []
    assert second == first


def test_model_diff_reads_the_snapshot_once_and_the_workspace_every_time(
    pair: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, snapshot, _ = pair
    snapshot_reads = reads_under(monkeypatch, snapshot)
    first = model_diff(workspace, snapshot)
    assert any(path.suffix == ".sql" for path in snapshot_reads)
    snapshot_reads.clear()
    workspace_reads = reads_under(monkeypatch, workspace)
    assert model_diff(workspace, snapshot) == first
    assert snapshot_reads == []
    assert any(path.suffix == ".sql" for path in workspace_reads)


def test_model_diff_sees_new_content_of_an_unchanged_file_list(
    pair: tuple[Path, Path, Path],
) -> None:
    workspace, snapshot, _ = pair
    model_diff(workspace, snapshot)
    stg = workspace / "project" / "models" / "staging" / "stg_orders.sql"
    shutil.copyfile(snapshot / "project" / "models" / "staging" / "stg_orders.sql", stg)
    assert "stg_orders" not in {d["model"] for d in model_diff(workspace, snapshot)}
    stg.write_text(stg.read_text() + "\n-- edited\nwhere 1 = 1\n")
    assert "stg_orders" in {d["model"] for d in model_diff(workspace, snapshot)}


def settled(root: Path) -> None:
    """Date `root` and every directory under it an hour back, as a workspace the agent has not
    touched for a while; a directory changed within the last moments is walked every time."""
    past = time.time_ns() - 3600 * 10**9
    for directory in [root, *(path for path in root.rglob("*") if path.is_dir())]:
        os.utime(directory, ns=(past, past))


def cached_then(project: Path, root: Path) -> None:
    """Fill the cache for `project` from settled directories, so the change that follows is
    seen only through a directory mtime differing from the one the walk recorded."""
    settled(root)
    model_files(project)


def test_model_files_follows_added_removed_and_renamed_files(tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / "models" / "sub").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: p\n")
    (project / "models" / "a.sql").write_text("select 1")
    cached_then(project, tmp_path)
    assert set(model_files(project)) == {"a"}

    cached_then(project, tmp_path)
    (project / "models" / "sub" / "b.sql").write_text("select 2")
    assert set(model_files(project)) == {"a", "b"}

    cached_then(project, tmp_path)
    (project / "models" / "sub" / "b.sql").rename(project / "models" / "sub" / "c.sql")
    assert model_files(project) == {
        "a": project / "models" / "a.sql",
        "c": project / "models" / "sub" / "c.sql",
    }

    cached_then(project, tmp_path)
    (project / "models" / "a.sql").unlink()
    assert set(model_files(project)) == {"c"}

    cached_then(project, tmp_path)
    (project / "models" / "deeper" / "x").mkdir(parents=True)
    (project / "models" / "deeper" / "x" / "d.sql").write_text("select 4")
    assert set(model_files(project)) == {"c", "d"}


def test_model_files_sees_a_model_directory_created_later(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: p\nmodel-paths: ['src/models']\n")
    cached_then(project, tmp_path)
    assert model_files(project) == {}
    (project / "src" / "models").mkdir(parents=True)
    (project / "src" / "models" / "a.sql").write_text("select 1")
    assert set(model_files(project)) == {"a"}


def test_model_files_drops_a_symlinked_model_whose_target_is_gone(tmp_path: Path) -> None:
    """Deleting a symlink's target changes no directory the walk listed."""
    project = tmp_path / "p"
    (project / "models").mkdir(parents=True)
    (project / "shared").mkdir()
    (project / "dbt_project.yml").write_text("name: p\n")
    (project / "models" / "a.sql").write_text("select 1")
    (project / "shared" / "b.sql").write_text("select 2")
    (project / "models" / "b.sql").symlink_to(project / "shared" / "b.sql")
    cached_then(project, tmp_path)
    assert set(model_files(project)) == {"a", "b"}
    (project / "shared" / "b.sql").unlink()
    assert set(model_files(project)) == {"a"}


def test_model_files_sees_a_file_added_without_a_visible_mtime_change(tmp_path: Path) -> None:
    """A file system stamps mtimes with a coarse clock, so a file added within the same tick
    as a walk leaves its directory's mtime as the walk saw it; a directory changed that
    recently is not trusted. (The stamp is set ahead so the test does not depend on how fast
    it runs.)"""
    project = tmp_path / "p"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: p\n")
    (models / "a.sql").write_text("select 1")
    stamp = time.time_ns() + 60 * 10**9
    os.utime(models, ns=(stamp, stamp))
    assert set(model_files(project)) == {"a"}
    (models / "b.sql").write_text("select 2")
    os.utime(models, ns=(stamp, stamp))
    assert set(model_files(project)) == {"a", "b"}


def test_model_files_walks_an_unchanged_project_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "p"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: p\n")
    (project / "models" / "a.sql").write_text("select 1")
    settled(tmp_path)
    listed: list[str] = []
    original = project_module.os.walk

    def counted(top: Any, *args: Any, **kwargs: Any) -> Any:
        listed.append(str(top))
        return original(top, *args, **kwargs)

    monkeypatch.setattr(project_module.os, "walk", counted)
    first = model_files(project)
    assert listed
    listed.clear()
    assert model_files(project) == first
    assert listed == []


def test_concurrent_calls_compute_each_cache_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP runs synchronous tools in a thread pool: a batch of calls fills each cache once."""
    workspace, snapshot = tmp_path / "ws", tmp_path / "snap"
    shutil.copytree(AFTER, workspace / "project")
    shutil.copytree(BEFORE, snapshot / "project")
    project = workspace / "project"
    settled(workspace)
    loads: list[str] = []
    original_load = project_module.yaml_lite.load
    walks: list[str] = []
    original_walk = project_module.os.walk

    def counted_load(text: str) -> Any:
        loads.append(text)
        return original_load(text)

    def counted_walk(top: Any, *args: Any, **kwargs: Any) -> Any:
        walks.append(str(top))
        return original_walk(top, *args, **kwargs)

    monkeypatch.setattr(project_module.yaml_lite, "load", counted_load)
    monkeypatch.setattr(project_module.os, "walk", counted_walk)
    config = project_module.project_config(project)
    barrier = threading.Barrier(16)
    results: dict[int, tuple[Any, Any]] = {}

    def call(i: int) -> None:
        barrier.wait()
        results[i] = (snapshot_documents(snapshot), model_files(project, config))

    threads = [threading.Thread(target=call, args=(i,)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 16
    assert all(result == results[0] for result in results.values())
    single_documents = snapshot_documents(snapshot)
    yml_loads = len(single_documents) + 1  # each YML file plus the snapshot's dbt_project.yml
    assert len(loads) == yml_loads + 1  # plus the workspace's dbt_project.yml read above
    model_walks = [top for top in walks if top.startswith(str(project / "models"))]
    assert len(model_walks) == 1
