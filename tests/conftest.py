import pytest

from backend import store


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point the store at a throwaway database and migrate it.

    Pipeline/store functions read store.DB_PATH at call time, so patching the module
    global redirects every layer without touching the real database.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(store, "DB_PATH", db_path)
    store.init_db()
    return db_path
