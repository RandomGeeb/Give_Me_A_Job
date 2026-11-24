import pytest

from jobbot import config


@pytest.fixture(autouse=True)
def temp_home(tmp_path, monkeypatch):
    """No test may touch real data."""
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "DOCS", tmp_path / "documents")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "t.db")
