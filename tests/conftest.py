"""`evals` lives at the repo root, outside the installed package; make it importable in-process
(tests that must replace the model, like the DAB runner's, cannot go through a subprocess)."""

import sys

from data_agent.run_config import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
