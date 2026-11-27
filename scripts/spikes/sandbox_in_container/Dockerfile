# Spike draft (ticket 152), not the product image (that is ticket 155): data-agent's code with
# its runtime dependencies, dbt with the DuckDB and Postgres adapters (dev-only in pyproject.toml,
# so the product image has to add them), and the docker CLI, which DockerSandbox runs.
# Build context: the repo root, with a .dockerignore that keeps .env*, runs/, .venv and
# node_modules out (155 adds it). Not built by probe.sh: on 2026-11-27 the dependency install
# finished but exporting the layer failed (colima's VM disk was full; the SDK bundles a large
# `claude` binary), so the probe runs python:3.12-slim with a venv on the host instead.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /usr/local/bin/uv
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src

WORKDIR /app
COPY pyproject.toml uv.lock ./
# The project itself is not installed (its wheel build needs the web UI); src/ is on PYTHONPATH.
RUN uv sync --frozen --no-dev --no-install-project \
    && uv pip install --python /opt/venv \
        "dbt-core==1.10.23" "dbt-duckdb==1.10.0" "dbt-postgres==1.10.2"
COPY src ./src
# Baked Skills break the docker sandbox: DockerSandbox bind-mounts config.skills_dir, a path
# the host daemon does not have (spike note, "155 的阻塞点"). Kept to show the gap.
COPY skills ./skills
# A named volume starts with the ownership of its mount point in the image.
RUN mkdir /vol && chown 10001:10001 /vol

# Not root, no home; `docker run --group-add <socket gid>` gives it the Docker socket.
USER 10001:10001
ENV HOME=/tmp
CMD ["python", "-m", "data_agent", "web"]
