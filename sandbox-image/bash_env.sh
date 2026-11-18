# Sourced by bash before each `bash -c` (BASH_ENV, set in the Dockerfile), so every sandbox_exec
# call first writes the stub dbt profile for the workspace's dbt project as it is now. Commands
# started from that one inherit the variable below and skip it.
if [ -z "${SANDBOX_STUB_PROFILE:-}" ]; then
    python /opt/sandbox/stub_profile.py >/dev/null 2>&1 || true
    export SANDBOX_STUB_PROFILE=1
fi
