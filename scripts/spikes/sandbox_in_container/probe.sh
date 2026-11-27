#!/usr/bin/env bash
# Ticket 152's probe, one command on a machine with Docker (colima or Linux):
#   scripts/spikes/sandbox_in_container/probe.sh
# Starts Postgres and a data-agent container (the Docker socket mounted, a non-root user in the
# socket's group, canary secrets in its environment), runs probe.py in it, and prints a yes/no
# with evidence per question. No model is called.
#
# No image is built: the data-agent container is python:3.12-slim with data-agent's code, a venv
# and the docker CLI on a host directory mounted at the same path (the layout the probe tests),
# so almost nothing lands on the Docker VM's disk (colima's was full on 2026-11-27; Dockerfile
# here is the image draft for ticket 155). Postgres keeps its data in tmpfs.
#
# Everything it starts is named da-spike-152-*; the trap removes those containers, the volume, the
# network and runs/spike-152/. The output is kept in runs/spike-152-output.txt.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../../.." && pwd)
HERE="$REPO/scripts/spikes/sandbox_in_container"
PREFIX=da-spike-152
NET=$PREFIX-net
VOLUME=$PREFIX-vol
PG=$PREFIX-pg
AGENT=$PREFIX-agent
BASE_IMAGE=python:3.12-slim
UV_IMAGE=ghcr.io/astral-sh/uv:0.10.8
# Under $HOME: colima shares the home directory with its VM at the same path, and /tmp not.
BASE="$REPO/runs/spike-152"
APP="$BASE/app"
TOOLS="$BASE/tools"
VENV="$BASE/venv"
SAME="$BASE/same"
DIFF_HOST="$BASE/diff"
DIFF_IN=/srv/$PREFIX-diff
OUT="$REPO/runs/spike-152-output.txt"
AGENT_USER=10001:10001

cleanup() {
  echo "== cleanup"
  for name in $(docker ps -a --filter "name=^$PREFIX-" --format '{{.Names}}'); do
    docker rm -f "$name" >/dev/null && echo "removed container $name"
  done
  docker volume rm "$VOLUME" >/dev/null 2>&1 && echo "removed volume $VOLUME" || true
  docker network rm "$NET" >/dev/null 2>&1 && echo "removed network $NET" || true
  rm -rf "$BASE"
  echo "left: containers=[$(docker ps -a --filter "name=^$PREFIX-" --format '{{.Names}}')]" \
    "volumes=[$(docker volume ls -q --filter "name=^$PREFIX-")]"
}

# Copy one file out of an image onto the host disk.
copy_out() {
  local image=$1 path=$2 name=$PREFIX-copy
  docker create --name "$name" "$image" >/dev/null
  docker cp "$name:$path" "$TOOLS/" >/dev/null
  docker rm "$name" >/dev/null
}

main() {
  echo "== host: $(uname -sm); docker context $(docker context show)"
  docker info --format 'server {{.ServerVersion}}, {{.OperatingSystem}}, {{.NCPU}} CPUs'
  docker image inspect data-agent-sandbox >/dev/null 2>&1 \
    || docker build -t data-agent-sandbox "$REPO/sandbox-image"

  echo "== stage code, docker CLI, uv and a venv under $BASE (host disk)"
  mkdir -p "$APP/tests/fixtures" "$TOOLS" "$SAME" "$DIFF_HOST"
  cp "$REPO/pyproject.toml" "$REPO/uv.lock" "$HERE/probe.py" "$APP/"
  cp -R "$REPO/src" "$REPO/skills" "$APP/"
  cp -R "$REPO/tests/fixtures/shop.sql" "$REPO/tests/fixtures/dbt_shop" "$APP/tests/fixtures/"
  find "$APP" -name __pycache__ -prune -exec rm -rf {} +
  copy_out docker:cli /usr/local/bin/docker
  copy_out "$UV_IMAGE" /uv
  chmod 777 "$BASE" "$SAME" "$DIFF_HOST"
  # The venv as dbt_execute needs it: the runtime dependencies plus dbt and both adapters (dev-only
  # in pyproject.toml). Built by the same image and user that run the probe.
  # uv's cache is in tmpfs: extracting a wheel into it on colima's virtiofs mount failed
  # ("failed to set permissions", 2026-11-27).
  docker run --rm --name "$PREFIX-venv" --user "$AGENT_USER" -v "$BASE:$BASE" -w "$APP" \
    --tmpfs /uv-cache:uid=10001,gid=10001 \
    -e HOME=/tmp -e UV_CACHE_DIR=/uv-cache -e UV_PROJECT_ENVIRONMENT="$VENV" \
    -e UV_LINK_MODE=copy "$BASE_IMAGE" sh -c "
      $TOOLS/uv sync -q --frozen --no-dev --no-install-project --python /usr/local/bin/python3 &&
      $TOOLS/uv pip install -q --python $VENV/bin/python \
        dbt-core==1.10.23 dbt-duckdb==1.10.0 dbt-postgres==1.10.2"

  local sock_gid
  sock_gid=$(docker run --rm --name "$PREFIX-stat" -v /var/run/docker.sock:/s alpine stat -c %g /s)
  echo "== socket /var/run/docker.sock in the daemon's namespace: group $sock_gid"

  docker network create "$NET" >/dev/null
  docker volume create "$VOLUME" >/dev/null
  docker run --rm --name "$PREFIX-chown" -v "$VOLUME:/vol" alpine chown "$AGENT_USER" /vol

  # Generated per run: nothing secret-looking is committed, and the probe looks for these values.
  local pw canary
  pw=$(openssl rand -hex 12)
  canary=$(openssl rand -hex 8)
  docker run -d --name "$PG" --network "$NET" --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_USER=spike -e POSTGRES_PASSWORD="$pw" -e POSTGRES_DB=shop \
    postgres:16-alpine >/dev/null
  for _ in $(seq 60); do
    docker exec "$PG" pg_isready -U spike -d shop -h 127.0.0.1 >/dev/null 2>&1 && break
    sleep 1
  done

  echo "== run probe.py in $AGENT"
  docker run --rm --name "$AGENT" --network "$NET" --user "$AGENT_USER" \
    --group-add "$sock_gid" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$BASE:$BASE" -v "$DIFF_HOST:$DIFF_IN" -v "$VOLUME:/vol" \
    --tmpfs /baked:uid=10001,gid=10001 -e SPIKE_BAKED=/baked \
    -e HOME=/tmp -e PATH="$VENV/bin:$TOOLS:/usr/local/bin:/usr/bin:/bin" \
    -e PYTHONPATH="$APP/src" -e SPIKE_APP="$APP" \
    -e SPIKE_SAME="$SAME" -e SPIKE_DIFF="$DIFF_IN" -e SPIKE_VOL=/vol -e SPIKE_VOLUME="$VOLUME" \
    -e SPIKE_PG_HOST="$PG" \
    -e DA_SPIKE_PG_URL="postgresql://spike:$pw@$PG:5432/shop" \
    -e ANTHROPIC_API_KEY="canary-anthropic-$canary" \
    -e DATA_AGENT_WEB_TOKEN="canary-web-$canary" \
    -e SPIKE_CANARIES=ANTHROPIC_API_KEY,DATA_AGENT_WEB_TOKEN,DA_SPIKE_PG_URL \
    "$BASE_IMAGE" python "$APP/probe.py"
}

trap cleanup EXIT
mkdir -p "$REPO/runs"
main 2>&1 | tee "$OUT"
