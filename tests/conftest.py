"""`evals` lives at the repo root, outside the installed package; make it importable in-process
(tests that must replace the model, like the DAB runner's, cannot go through a subprocess).

Unit tests never reach Docker (.scratch/agent-tools/spec.md "Testing Decisions"): every run now has
a sandbox, the docker backend by default, so the docker calls of a test's own process go to a
stand-in (`fake_docker`) unless the test is an integration test.
"""

import sys

import pytest

from data_agent.run_config import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def no_real_docker(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("integration") is None:
        from fake_docker import install

        install(monkeypatch)


@pytest.fixture(autouse=True)
def own_dictionary_cache(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Product runs keep value dictionaries under `$XDG_CACHE_HOME` (`agent.dictionary_cache_dir`,
    ticket 144): each test gets its own, so no test reads one another test (or a real run) left."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("xdg-cache")))
