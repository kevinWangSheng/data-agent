# The docker backend's image for sandbox_exec (ticket 97; spec "`sandbox_exec`"): Python with the
# data packages and dbt, no credentials. Build: `make sandbox-image`.
FROM python:3.12-slim

# dbt and DuckDB as the data-eng-bench image pins them (plan §5).
RUN pip install --no-cache-dir \
        "duckdb==1.2.2" \
        "dbt-core>=1.10,<1.11" \
        "dbt-duckdb==1.10.0" \
        pandas \
        pyarrow \
        matplotlib

# dbt reads the stub profile from here, never a profile in the workspace. bash writes it before
# each command (bash_env.sh), so it names the profile of the project the workspace has then.
# Commands run as the host's user (`docker run --user`), who has no home in the image: /tmp is
# writable by anyone, and pip installs there (a user install) when site-packages is not.
ENV DBT_PROFILES_DIR=/tmp/stub-profiles \
    BASH_ENV=/opt/sandbox/bash_env.sh \
    HOME=/tmp
COPY stub_profile.py bash_env.sh /opt/sandbox/

WORKDIR /workspace
CMD ["sleep", "infinity"]
