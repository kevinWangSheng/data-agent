"""The data sources of the config's `[sources.<name>]` tables (ticket 78, spec "本地运行的数据源";
ticket 143 moved them from the `--data-sources` file into the config): each rule the server
checks at startup."""

from pathlib import Path

import pytest

from data_agent.config import load_effective_config
from data_agent.web.sources import DataSource, config_sources


def load_data_sources(path: Path) -> list[DataSource]:
    """The sources of a config file, as `data-agent web --config` reads them."""
    return config_sources(load_effective_config([path], {}, {}))


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.toml"
    path.write_text(text)
    return path


def test_reads_duckdb_and_postgres_sources_relative_to_the_file(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()
    path = write(
        tmp_path,
        """
[sources.shop]
kind = "duckdb"
path = "shop.duckdb"

[sources.warehouse]
kind = "postgres"
env = "WAREHOUSE_URL"
dbt_project = "proj"
""",
    )
    assert load_data_sources(path) == [
        DataSource(name="shop", duckdb=tmp_path / "shop.duckdb"),
        DataSource(name="warehouse", postgres_env="WAREHOUSE_URL", dbt_project=tmp_path / "proj"),
    ]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('kind = "mysql"\npath = "x"', "kind"),
        ('kind = ["duckdb"]\npath = "x"', "kind"),
        ('path = "x.duckdb"', "kind"),
        ('kind = "duckdb"', "path"),
        ('kind = "postgres"', "env"),
        ('kind = "duckdb"\npath = "x.duckdb"\ndbt_project = "missing"', "dbt_project"),
        ('kind = "duckdb"\npath = "x.duckdb"\nenv = "V"', "env"),
        ('kind = "postgres"\nenv = "V"\npath = "x.duckdb"', "path"),
        ('kind = "postgres"\nenv = "V"\nurl = "x"', "url"),
    ],
)
def test_a_bad_source_is_refused_naming_the_problem(
    tmp_path: Path, body: str, message: str
) -> None:
    path = write(tmp_path, f"[sources.s]\n{body}\n")
    with pytest.raises(ValueError, match=message) as error:
        load_data_sources(path)
    assert "sources.s" in str(error.value)


def test_a_connection_string_in_place_of_the_variable_name_is_refused_without_echoing_it(
    tmp_path: Path,
) -> None:
    url = "postgresql://agent:s3cret@db.example:5432/w"
    path = write(tmp_path, f'[sources.w]\nkind = "postgres"\nenv = "{url}"\n')
    with pytest.raises(ValueError, match="variable name") as error:
        load_data_sources(path)
    assert "s3cret" not in str(error.value)


def test_a_file_without_sources_has_none_and_other_tables_are_refused(tmp_path: Path) -> None:
    # A config needs no source (ticket 143): the web server then offers none.
    assert load_data_sources(write(tmp_path, "")) == []
    with pytest.raises(ValueError, match="other"):
        load_data_sources(
            write(tmp_path, '[other]\nx = 1\n[sources.s]\nkind = "duckdb"\npath = "a"\n')
        )


def test_a_missing_or_malformed_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        load_data_sources(tmp_path / "nope.toml")
    with pytest.raises(ValueError, match="TOML"):
        load_data_sources(write(tmp_path, "[sources\n"))
