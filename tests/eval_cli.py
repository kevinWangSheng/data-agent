"""Invoke the real evals CLI in-process for tests of its behavior."""

import os
import subprocess
import sys

import pytest

from data_agent.run_config import REPO_ROOT
from evals.ablate import evals_cli


def run_evals(*args: str, clean_model_env: bool = False) -> subprocess.CompletedProcess[str]:
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(REPO_ROOT)
        if clean_model_env:
            for name in tuple(os.environ):
                if name.startswith("DATA_AGENT_"):
                    patch.delenv(name)
        code, out, err = evals_cli(list(args))
    return subprocess.CompletedProcess([sys.executable, "-m", "evals", *args], code, out, err)


def export(*args: str) -> subprocess.CompletedProcess[str]:
    return run_evals("export", *args, clean_model_env=True)
