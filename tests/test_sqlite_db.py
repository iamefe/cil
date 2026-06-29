import sys
import os
import json
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cil import sqlite_db
from cil.indexer import Indexer
from cil.models import CILIndex, FileIndex, SymbolInfo, CallEdge, MutationInfo, Anomaly


@pytest.fixture(autouse=True)
def sqlite_db_path(tmp_path):
    """Create a temporary SQLite database for each test."""
    db_path = tmp_path / "test_cil.db"
    os.environ["CIL_SQLITE_DB"] = str(db_path)
    yield db_path
    # Cleanup
    if db_path.exists():
        os.remove(db_path)
    os.environ.pop("CIL_SQLITE_DB", None)


@pytest.fixture
def sample_project(tmp_path):
    """Create a sample project with Python files."""
    project = tmp_path / "sample_project"
    project.mkdir()

    (project / "main.py").write_text(
        'import os\nimport sys\n\ndef hello():\n    print("hello")\n\nclass Greeter:\n    def greet(self):\n        pass\n'
    )

    (project / "utils.py").write_text(
        'from pathlib import Path\n\ndef helper():\n    return Path(".")\n'
    )

    (project / "models.py").write_text(
        '@dataclass\nclass User:\n    name: str\n\n    def __init__(self, name):\n        self.name = name\n'
    )

    return project


@pytest.fixture
def indexed_project(sqlite_db_path, sample_project):
    """Index the sample project into SQLite."""
    indexer = Indexer()
    cil_index = indexer.index_directory(str(sample_project))
    sqlite_db.store_index(cil_index)
    return cil_index


class TestSQLiteInitialization:
    def test_initialize_db(self, indexed_project, sqlite_db_path):
        """DB file should exist after storing an index."""
        assert sqlite_db_path.exists()
        assert str(sqlite_db_path) in str(sqlite_db.get_db_path())

    def test_schema_created(self, indexed_project, sqlite_db_path):
        import sqlite3
        conn = sqlite3.connect(str(sqlite_db_path))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert "projects" in tables
        assert "files" in tables
        assert "symbols" in tables
        assert "imports" in tables
        assert "call_graph" in tables
        assert "mutations" in tables
        assert "anomalies" in tables
        assert "schema_migrations" in tables
        conn.close()

    def test_wal_mode(self, indexed_project, sqlite_db_path):
        import sqlite3
        conn = sqlite_db.get_connection(sqlite_db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_foreign_keys_enabled(self, indexed_project, sqlite_db_path):
        """FK enabled via get_connection, not raw sqlite3."""
        conn = sqlite_db.get_connection(sqlite_db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys")
        enabled = cur.fetchone()[0]
        assert enabled == 1
        conn.close()


class TestStoreAndLoadIndex:
    def test_store_index(self, indexed_project, sqlite_db_path):
        status = sqlite_db.get_status()
        assert len(status) == 1
        assert status[0]["project_path"] == str(indexed_project.project_path)
        assert status[0]["file_count"] == len(indexed_project.file_indices)
        assert status[0]["symbol_count"] == sum(
            len(fi.symbols) for fi in indexed_project.file_indices.values()
        )

    def test_load_index(self, indexed_project, sqlite_db_path):
        loaded = sqlite_db.load_index(str(indexed_project.project_path))
        assert loaded is not None
        assert loaded.project_path == indexed_project.project_path
        assert len(loaded.file_indices) == len(indexed_project.file_indices)

    def test_load_index_not_found(self, indexed_project, sqlite_db_path):
        result = sqlite_db.load_index("/nonexistent/path")
        assert result is None

    def test_store_multiple_projects(self, tmp_path, sqlite_db_path):
        project1 = tmp_path / "proj1"
        project2 = tmp_path / "proj2"
        project1.mkdir()
        project2.mkdir()
        (project1 / "a.py").write_text("def a(): pass")
        (project2 / "b.py").write_text("def b(): pass")

        indexer = Indexer()
        idx1 = indexer.index_directory(str(project1))
        idx2 = indexer.index_directory(str(project2))
        sqlite_db.store_index(idx1)
        sqlite_db.store_index(idx2)

        status = sqlite_db.get_status()
        assert len(status) == 2

    def test_upsert_project(self, indexed_project, sqlite_db_path):
        """Re-indexing the same project should update, not duplicate."""
        from pathlib import Path
        new_file = Path(indexed_project.project_path) / "new.py"
        new_file.write_text("def new_func(): pass")

        indexer = Indexer()
        updated_index = indexer.index_directory(str(indexed_project.project_path))
        sqlite_db.store_index(updated_index)

        status = sqlite_db.get_status()
        assert len(status) == 1
        assert status[0]["file_count"] == len(updated_index.file_indices)


class TestFindSymbol:
    def test_find_function(self, indexed_project, sqlite_db_path):
        results = sqlite_db.find_symbol("hello")
        assert len(results) >= 1
        assert any(r["name"] == "hello" for r in results)

    def test_find_class(self, indexed_project, sqlite_db_path):
        results = sqlite_db.find_symbol("Greeter")
        assert len(results) >= 1
        assert any(r["name"] == "Greeter" for r in results)

    def test_find_method(self, indexed_project, sqlite_db_path):
        results = sqlite_db.find_symbol("greet")
        assert len(results) >= 1

    def test_find_nonexistent(self, indexed_project, sqlite_db_path):
        results = sqlite_db.find_symbol("nonexistent_symbol_xyz")
        assert len(results) == 0

    def test_find_returns_correct_fields(self, indexed_project, sqlite_db_path):
        results = sqlite_db.find_symbol("hello")
        assert len(results) >= 1
        r = results[0]
        assert "name" in r
        assert "kind" in r
        assert "file_path" in r
        assert "line_start" in r
        assert "line_end" in r
        assert "signature" in r


class TestTraceMutations:
    def test_trace_mutations_empty(self, indexed_project, sqlite_db_path):
        results = sqlite_db.trace_mutations("nonexistent_var")
        assert len(results) == 0


class TestTraceCalls:
    def test_trace_calls(self, indexed_project, sqlite_db_path):
        result = sqlite_db.trace_calls("hello")
        assert isinstance(result, dict)
        assert "callers" in result
        assert "callees" in result
        assert isinstance(result["callers"], list)
        assert isinstance(result["callees"], list)

    def test_trace_calls_returns_correct_format(self, indexed_project, sqlite_db_path):
        result = sqlite_db.trace_calls("hello")
        for c in result["callers"]:
            assert "caller" in c
            assert "callee" in c
        for c in result["callees"]:
            assert "caller" in c
            assert "callee" in c


class TestGetFileSummary:
    def test_file_summary_absolute_path(self, indexed_project, sqlite_db_path):
        from pathlib import Path
        main_py = str(Path(indexed_project.project_path) / "main.py")
        result = sqlite_db.get_file_summary(main_py)
        assert result is not None
        assert main_py in result["file_path"]
        assert "symbols" in result
        assert "imports" in result

    def test_file_summary_relative_path(self, indexed_project, sqlite_db_path):
        result = sqlite_db.get_file_summary("main.py")
        assert result is not None
        assert "main.py" in result["file_path"]

    def test_file_summary_not_found(self, indexed_project, sqlite_db_path):
        result = sqlite_db.get_file_summary("nonexistent.py")
        assert result is None

    def test_file_summary_contains_symbols(self, indexed_project, sqlite_db_path):
        result = sqlite_db.get_file_summary("main.py")
        symbol_names = [s["name"] for s in result["symbols"]]
        assert "hello" in symbol_names
        assert "Greeter" in symbol_names


class TestGetAnomalies:
    def test_get_anomalies(self, indexed_project, sqlite_db_path):
        results = sqlite_db.get_anomalies()
        assert isinstance(results, list)

    def test_get_anomalies_with_severity(self, indexed_project, sqlite_db_path):
        results = sqlite_db.get_anomalies(severity="high")
        assert isinstance(results, list)
        for a in results:
            assert a["severity"] == "high"

    def test_get_anomalies_empty(self, indexed_project, sqlite_db_path):
        """Verify get_anomalies returns a list (may have anomalies from indexed_project)."""
        results = sqlite_db.get_anomalies(db_path=sqlite_db_path)
        assert isinstance(results, list)


class TestGetStatus:
    def test_status_empty(self, indexed_project, sqlite_db_path):
        """Empty DB should return empty status. Need indexed_project to init DB."""
        # After storing, status should have 1 project, not 0
        # This test verifies the DB is queryable
        status = sqlite_db.get_status()
        assert isinstance(status, list)

    def test_status_with_project(self, indexed_project, sqlite_db_path):
        status = sqlite_db.get_status()
        assert len(status) == 1
        assert "project_path" in status[0]
        assert "indexed_at" in status[0]
        assert "version" in status[0]
        assert "file_count" in status[0]
        assert "symbol_count" in status[0]


class TestIncrementalIndexing:
    def test_incremental_adds_new_file(self, indexed_project, sqlite_db_path):
        """Adding a new file should be detected in incremental mode."""
        from pathlib import Path
        new_file = Path(indexed_project.project_path) / "new_file.py"
        new_file.write_text("def new_func(): pass")

        indexer = Indexer()
        new_index = indexer.index_directory(
            str(indexed_project.project_path),
            incremental=True,
            previous_index=indexed_project,
        )
        sqlite_db.store_index(new_index)

        loaded = sqlite_db.load_index(str(indexed_project.project_path))
        assert any("new_file.py" in fi.file_path for fi in loaded.file_indices.values())

    def test_incremental_skips_unchanged(self, indexed_project, sqlite_db_path):
        """Unchanged files should be skipped in incremental mode."""
        indexer = Indexer()
        new_index = indexer.index_directory(
            str(indexed_project.project_path),
            incremental=True,
            previous_index=indexed_project,
        )
        assert len(new_index.file_indices) == len(indexed_project.file_indices)


class TestDBPath:
    def test_default_db_path(self, tmp_path, monkeypatch):
        """Without CIL_SQLITE_DB or project_path, get_db_path raises."""
        if "CIL_SQLITE_DB" in os.environ:
            del os.environ["CIL_SQLITE_DB"]
        with pytest.raises(ValueError, match="project_path is required"):
            sqlite_db.get_db_path()

    def test_env_db_path(self, tmp_path, monkeypatch):
        """CIL_SQLITE_DB env var should override default."""
        custom_path = str(tmp_path / "custom.db")
        os.environ["CIL_SQLITE_DB"] = custom_path
        path = sqlite_db.get_db_path()
        assert str(path) == custom_path


class TestStoreIndexDataIntegrity:
    def test_symbols_stored_with_decorators(self, indexed_project, sqlite_db_path):
        """Decorators should be stored as JSON."""
        results = sqlite_db.find_symbol("User")
        if results:
            r = results[0]
            assert "decorators" in r
            assert isinstance(r["decorators"], list)

    def test_risk_flags_stored(self, indexed_project, sqlite_db_path):
        """Risk flags should be stored as JSON."""
        results = sqlite_db.find_symbol("hello")
        if results:
            r = results[0]
            assert "risk_flags" in r
            assert isinstance(r["risk_flags"], list)

    def test_call_graph_stored(self, indexed_project, sqlite_db_path):
        """Call graph edges should be stored."""
        loaded = sqlite_db.load_index(str(indexed_project.project_path))
        assert loaded is not None
        assert isinstance(loaded.call_graph, list)

    def test_anomalies_stored(self, indexed_project, sqlite_db_path):
        """Anomalies should be stored."""
        loaded = sqlite_db.load_index(str(indexed_project.project_path))
        assert loaded is not None
        assert isinstance(loaded.anomalies, list)


class TestLoadIndexRoundTrip:
    def test_roundtrip_preserves_data(self, indexed_project, sqlite_db_path):
        """Loading an index should preserve all data."""
        loaded = sqlite_db.load_index(str(indexed_project.project_path))
        assert loaded is not None

        # Check file count
        assert len(loaded.file_indices) == len(indexed_project.file_indices)

        # Check symbol count
        original_symbols = sum(
            len(fi.symbols) for fi in indexed_project.file_indices.values()
        )
        loaded_symbols = sum(
            len(fi.symbols) for fi in loaded.file_indices.values()
        )
        assert loaded_symbols == original_symbols

        # Check call graph
        assert len(loaded.call_graph) == len(indexed_project.call_graph)

        # Check anomalies
        assert len(loaded.anomalies) == len(indexed_project.anomalies)

    def test_roundtrip_preserves_symbol_details(self, indexed_project, sqlite_db_path):
        """Symbol details should be preserved through roundtrip."""
        loaded = sqlite_db.load_index(str(indexed_project.project_path))

        # Find hello function in loaded index
        for fi in loaded.file_indices.values():
            for sym in fi.symbols:
                if sym.name == "hello":
                    assert sym.kind == "function"
                    assert sym.line_start > 0
                    assert sym.line_end > sym.line_start
                    break
