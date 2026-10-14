# Tests for how the FFC API key is found. Run locally with:  pytest tests
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.delenv("FFC_API_KEY", raising=False)
    monkeypatch.setattr(config, "ENV_FILES", [str(tmp_path / ".env")])
    monkeypatch.delitem(sys.modules, "databricks", raising=False)
    monkeypatch.delitem(sys.modules, "databricks.sdk", raising=False)
    monkeypatch.delitem(sys.modules, "databricks.sdk.runtime", raising=False)


def write_env(tmp_path, text):
    (tmp_path / ".env").write_text(text, encoding="utf-8")


def test_reads_the_key_from_a_dotenv_file(tmp_path):
    write_env(tmp_path, "FFC_API_KEY=abc123\n")
    assert config.get_ffc_api_key() == "abc123"


@pytest.mark.parametrize("line", [
    'FFC_API_KEY="abc123"',
    "FFC_API_KEY='abc123'",
    "export FFC_API_KEY=abc123",
    "  FFC_API_KEY = abc123  ",
])
def test_dotenv_formats_are_handled(tmp_path, line):
    write_env(tmp_path, "# a comment\n\nOTHER=x\n" + line + "\n")
    assert config.get_ffc_api_key() == "abc123"


def test_environment_variable_beats_dotenv(tmp_path, monkeypatch):
    write_env(tmp_path, "FFC_API_KEY=from_file\n")
    monkeypatch.setenv("FFC_API_KEY", "from_env")
    assert config.get_ffc_api_key() == "from_env"


def test_databricks_secret_beats_everything(tmp_path, monkeypatch):
    write_env(tmp_path, "FFC_API_KEY=from_file\n")
    monkeypatch.setenv("FFC_API_KEY", "from_env")
    secrets = types.SimpleNamespace(get=lambda scope, key: "from_secret")
    runtime = types.ModuleType("databricks.sdk.runtime")
    runtime.dbutils = types.SimpleNamespace(secrets=secrets)
    for name in ("databricks", "databricks.sdk"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "databricks.sdk.runtime", runtime)
    assert config.get_ffc_api_key() == "from_secret"


def test_a_missing_secret_falls_back_to_dotenv(tmp_path, monkeypatch):
    write_env(tmp_path, "FFC_API_KEY=from_file\n")

    def missing(scope, key):
        raise Exception("Secret does not exist")

    runtime = types.ModuleType("databricks.sdk.runtime")
    runtime.dbutils = types.SimpleNamespace(secrets=types.SimpleNamespace(get=missing))
    for name in ("databricks", "databricks.sdk"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "databricks.sdk.runtime", runtime)
    assert config.get_ffc_api_key() == "from_file"


def test_no_key_anywhere_raises_and_says_where_to_put_it():
    with pytest.raises(RuntimeError) as err:
        config.get_ffc_api_key()
    message = str(err.value)
    assert "secret" in message and "FFC_API_KEY" in message and ".env" in message


def test_the_key_is_never_written_to_the_environment(tmp_path):
    write_env(tmp_path, "FFC_API_KEY=abc123\n")
    config.get_ffc_api_key()
    assert "FFC_API_KEY" not in os.environ


def test_dotenv_is_gitignored():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert ".env" in open(os.path.join(root, ".gitignore")).read().split()


def test_a_dotenv_outside_the_repo_is_found_when_the_one_inside_is_missing(tmp_path, monkeypatch):
    repo = tmp_path / "fgs_pipeline"
    repo.mkdir()
    (tmp_path / ".env").write_text("FFC_API_KEY=from_parent\n")        # one level up, outside the repo
    monkeypatch.setattr(config, "ENV_FILES", [str(repo / ".env"), str(tmp_path / ".env")])
    assert config.get_ffc_api_key() == "from_parent"


def test_the_file_inside_the_repo_wins_over_the_one_outside(tmp_path, monkeypatch):
    repo = tmp_path / "fgs_pipeline"
    repo.mkdir()
    (repo / ".env").write_text("FFC_API_KEY=inside\n")
    (tmp_path / ".env").write_text("FFC_API_KEY=outside\n")
    monkeypatch.setattr(config, "ENV_FILES", [str(repo / ".env"), str(tmp_path / ".env")])
    assert config.get_ffc_api_key() == "inside"


def test_default_search_order_covers_inside_then_outside_the_repo(monkeypatch):
    monkeypatch.delenv("FGS_ENV_FILE", raising=False)
    import importlib
    reloaded = importlib.reload(config)
    repo = os.path.dirname(os.path.abspath(reloaded.__file__))
    assert reloaded.ENV_FILES == [os.path.join(repo, ".env"),
                                  os.path.join(os.path.dirname(repo), ".env")]


def test_fgs_env_file_override_is_searched_first(monkeypatch, tmp_path):
    monkeypatch.setenv("FGS_ENV_FILE", str(tmp_path / "custom.env"))
    import importlib
    reloaded = importlib.reload(config)
    assert reloaded.ENV_FILES[0] == str(tmp_path / "custom.env") and len(reloaded.ENV_FILES) == 3
