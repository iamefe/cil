import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional


PROJECTS_DIR = Path.home() / ".cil" / "projects"


SCHEMA_VERSION = 1


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL UNIQUE,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT NOT NULL UNIQUE,
    indexed_at TIMESTAMP NOT NULL,
    version INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT DEFAULT '',
    indexed_at TIMESTAMP NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, file_path)
);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    signature TEXT DEFAULT '',
    docstring TEXT DEFAULT '',
    decorators TEXT DEFAULT '[]',
    purpose TEXT DEFAULT '',
    risk_flags TEXT DEFAULT '[]',
    complexity TEXT DEFAULT '',
    audit_notes TEXT DEFAULT '',
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    import_path TEXT NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS call_graph (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    caller TEXT NOT NULL,
    callee TEXT NOT NULL,
    line INTEGER NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mutations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    target TEXT NOT NULL,
    source TEXT NOT NULL,
    line INTEGER NOT NULL,
    kind TEXT DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line INTEGER NOT NULL,
    message TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_call_graph_caller ON call_graph(caller);
CREATE INDEX IF NOT EXISTS idx_call_graph_callee ON call_graph(callee);
CREATE INDEX IF NOT EXISTS idx_mutations_target ON mutations(target);
CREATE INDEX IF NOT EXISTS idx_anomalies_severity ON anomalies(severity);
CREATE INDEX IF NOT EXISTS idx_anomalies_type ON anomalies(type);
CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
"""


def get_project_name(project_path: str) -> str:
    """Derive a project name from the project path."""
    return Path(project_path).stem


def list_project_dbs() -> list[Path]:
    """List all per-project database files."""
    if not PROJECTS_DIR.exists():
        return []
    return sorted(PROJECTS_DIR.rglob("*.db"))


def get_project_db_path(project_path: str) -> Path:
    """Get the per-project SQLite database path.

    Uses CIL_SQLITE_DB env var if set (legacy single-DB mode),
    otherwise returns ~/.cil/projects/<project>/<project>.db
    """
    db_path = os.environ.get("CIL_SQLITE_DB")
    if db_path:
        return Path(db_path)
    name = get_project_name(project_path)
    return PROJECTS_DIR / name / f"{name}.db"


def get_db_path(project_path: Optional[str] = None) -> Path:
    """Get the SQLite database path.

    CIL_SQLITE_DB env var always takes precedence (single-DB mode).
    If project_path is provided and no env var, returns per-project DB path.
    Otherwise raises.
    """
    env_path = os.environ.get("CIL_SQLITE_DB")
    if env_path:
        return Path(env_path)
    if project_path:
        return get_project_db_path(project_path)
    raise ValueError("project_path is required when CIL_SQLITE_DB is not set")


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a SQLite connection with row factory."""
    if db_path is None:
        raise ValueError("db_path must be provided")
    path = db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize_db(project_path: Optional[str] = None, db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Initialize the database schema if not already done."""
    path = db_path or get_db_path(project_path)
    conn = get_connection(path)
    conn.executescript(CREATE_TABLES_SQL)

    # Check if schema version exists
    cursor = conn.execute("SELECT version FROM schema_migrations WHERE version = ?", (SCHEMA_VERSION,))
    if not cursor.fetchone():
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()

    return conn


def drop_all(project_path: str, db_path: Optional[Path] = None) -> None:
    """Drop all tables (for testing)."""
    path = db_path or get_db_path(project_path)
    conn = get_connection(path)
    conn.executescript("""
        DROP TABLE IF EXISTS anomalies;
        DROP TABLE IF EXISTS mutations;
        DROP TABLE IF EXISTS call_graph;
        DROP TABLE IF EXISTS imports;
        DROP TABLE IF EXISTS symbols;
        DROP TABLE IF EXISTS files;
        DROP TABLE IF EXISTS projects;
        DROP TABLE IF EXISTS schema_migrations;
    """)
    conn.commit()
    conn.close()


def project_exists(project_path: str, db_path: Optional[Path] = None) -> bool:
    """Check if a project is indexed."""
    path = db_path or get_db_path(project_path)
    conn = get_connection(path)
    cursor = conn.execute("SELECT 1 FROM projects WHERE project_path = ?", (project_path,))
    return cursor.fetchone() is not None


def get_project_id(project_path: str, db_path: Optional[Path] = None) -> Optional[int]:
    """Get the project ID by path."""
    path = db_path or get_db_path(project_path)
    conn = get_connection(path)
    cursor = conn.execute("SELECT id FROM projects WHERE project_path = ?", (project_path,))
    row = cursor.fetchone()
    return row["id"] if row else None


def upsert_project(project_path: str, version: int = 1, db_path: Optional[Path] = None) -> int:
    """Insert or update a project. Returns the project ID."""
    path = db_path or get_db_path(project_path)
    conn = get_connection(path)
    conn.execute(
        """
        INSERT INTO projects (project_path, indexed_at, version)
        VALUES (?, datetime('now'), ?)
        ON CONFLICT(project_path) DO UPDATE SET
            indexed_at = datetime('now'),
            version = excluded.version
        """,
        (project_path, version),
    )
    conn.commit()
    return get_project_id(project_path, db_path)


def delete_project(project_path: str, db_path: Optional[Path] = None) -> None:
    """Delete a project and all its associated data."""
    path = db_path or get_db_path(project_path)
    conn = get_connection(path)
    project_id = get_project_id(project_path, db_path)
    if project_id:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()


def remove_project(project_path: str) -> None:
    """Remove a project record and delete its per-project DB file."""
    db_path = get_project_db_path(project_path)
    if db_path.exists():
        try:
            conn = get_connection(db_path)
            project_id = get_project_id(project_path, db_path)
            if project_id:
                conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                conn.commit()
        except sqlite3.OperationalError:
            pass
        db_path.unlink()
    proj_dir = db_path.parent
    if proj_dir.exists() and not any(proj_dir.iterdir()):
        proj_dir.rmdir()


def _get_db_path_for_project(project_path: str, db_path: Optional[Path] = None) -> Path:
    """Get the DB path for a project, using provided db_path or deriving from project_path."""
    if db_path is not None:
        return db_path
    return get_project_db_path(project_path)


def _scan_all_projects(func):
    """Decorator that scans all per-project DBs and aggregates results."""
    def wrapper(*args, **kwargs):
        dbs = list_project_dbs()
        all_results = []
        for db_path in dbs:
            try:
                result = func(db_path=db_path, *args, **kwargs)
                if isinstance(result, list):
                    all_results.extend(result)
                elif isinstance(result, dict):
                    for k, v in result.items():
                        if k not in all_results:
                            all_results[k] = []
                        if isinstance(v, list):
                            all_results[k].extend(v)
                        else:
                            all_results[k].append(v)
                else:
                    all_results.append(result)
            except Exception:
                continue
        if isinstance(all_results, dict):
            return all_results
        return all_results
    return wrapper


def upsert_file(project_id: int, file_path: str, file_hash: str, project_path: str, db_path: Optional[Path] = None) -> int:
    """Insert or update a file. Returns the file ID."""
    path = _get_db_path_for_project(project_path, db_path)
    conn = get_connection(path)
    conn.execute(
        """
        INSERT INTO files (project_id, file_path, file_hash, indexed_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(project_id, file_path) DO UPDATE SET
            file_hash = excluded.file_hash,
            indexed_at = datetime('now')
        """,
        (project_id, file_path, file_hash),
    )
    conn.commit()
    cursor = conn.execute(
        "SELECT id FROM files WHERE project_id = ? AND file_path = ?",
        (project_id, file_path),
    )
    return cursor.fetchone()["id"]


def insert_symbols(file_id: int, symbols: list[dict], project_path: str, db_path: Optional[Path] = None) -> None:
    """Insert symbols for a file."""
    path = _get_db_path_for_project(project_path, db_path)
    conn = get_connection(path)
    for sym in symbols:
        conn.execute(
            """
            INSERT INTO symbols (
                file_id, name, kind, line_start, line_end,
                signature, docstring, decorators,
                purpose, risk_flags, complexity, audit_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                sym["name"],
                sym["kind"],
                sym["line_start"],
                sym["line_end"],
                sym.get("signature", ""),
                sym.get("docstring", ""),
                json.dumps(sym.get("decorators", [])),
                sym.get("purpose", ""),
                json.dumps(sym.get("risk_flags", [])),
                sym.get("complexity", ""),
                sym.get("audit_notes", ""),
            ),
        )
    conn.commit()


def insert_imports(file_id: int, imports: list[str], project_path: str, db_path: Optional[Path] = None) -> None:
    """Insert imports for a file."""
    path = _get_db_path_for_project(project_path, db_path)
    conn = get_connection(path)
    for imp in imports:
        conn.execute(
            "INSERT INTO imports (file_id, import_path) VALUES (?, ?)",
            (file_id, imp),
        )
    conn.commit()


def insert_call_graph(project_id: int, edges: list[dict], project_path: str, db_path: Optional[Path] = None) -> None:
    """Insert call graph edges for a project."""
    path = _get_db_path_for_project(project_path, db_path)
    conn = get_connection(path)
    for edge in edges:
        conn.execute(
            "INSERT INTO call_graph (project_id, caller, callee, line) VALUES (?, ?, ?, ?)",
            (project_id, edge["caller"], edge["callee"], edge["line"]),
        )
    conn.commit()


def insert_mutations(project_id: int, mutations: list[dict], project_path: str, db_path: Optional[Path] = None) -> None:
    """Insert mutations for a project."""
    path = _get_db_path_for_project(project_path, db_path)
    conn = get_connection(path)
    for m in mutations:
        conn.execute(
            "INSERT INTO mutations (project_id, target, source, line, kind) VALUES (?, ?, ?, ?, ?)",
            (project_id, m["target"], m["source"], m["line"], m.get("kind", "")),
        )
    conn.commit()


def insert_anomalies(project_id: int, anomalies: list[dict], project_path: str, db_path: Optional[Path] = None) -> None:
    """Insert anomalies for a project."""
    path = _get_db_path_for_project(project_path, db_path)
    conn = get_connection(path)
    for a in anomalies:
        conn.execute(
            "INSERT INTO anomalies (project_id, type, severity, file_path, line, message) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, a["type"], a["severity"], a["file_path"], a["line"], a["message"]),
        )
    conn.commit()


def clear_project_data(project_id: int, project_path: str, db_path: Optional[Path] = None) -> None:
    """Clear all data for a project (except the project record itself)."""
    path = _get_db_path_for_project(project_path, db_path)
    conn = get_connection(path)
    conn.execute("DELETE FROM anomalies WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM mutations WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM call_graph WHERE project_id = ?", (project_id,))

    # Delete symbols, imports, and files via project_id
    file_ids = [row["id"] for row in conn.execute("SELECT id FROM files WHERE project_id = ?", (project_id,)).fetchall()]
    for fid in file_ids:
        conn.execute("DELETE FROM symbols WHERE file_id = ?", (fid,))
        conn.execute("DELETE FROM imports WHERE file_id = ?", (fid,))
    conn.execute("DELETE FROM files WHERE project_id = ?", (project_id,))
    conn.commit()


def _find_symbol_in_db(name: str, db_path: Path) -> list[dict]:
    """Find a symbol in a single project DB."""
    conn = get_connection(db_path)
    cursor = conn.execute(
        """
        SELECT s.name, s.kind, s.line_start, s.line_end, s.signature, s.docstring,
               s.decorators, s.purpose, s.risk_flags, s.complexity, s.audit_notes,
               f.file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE LOWER(s.name) LIKE LOWER(?)
        """,
        (f"%{name}%",),
    )
    results = []
    for row in cursor.fetchall():
        results.append({
            "name": row["name"],
            "kind": row["kind"],
            "file_path": row["file_path"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "signature": row["signature"],
            "docstring": row["docstring"],
            "decorators": json.loads(row["decorators"]) if row["decorators"] else [],
            "purpose": row["purpose"],
            "risk_flags": json.loads(row["risk_flags"]) if row["risk_flags"] else [],
            "complexity": row["complexity"],
            "audit_notes": row["audit_notes"],
        })
    return results


def find_symbol(name: str, project_path: Optional[str] = None, db_path: Optional[Path] = None) -> list[dict]:
    """Find a symbol across all indexed files. If project_path is given, only search that project."""
    if project_path or db_path:
        path = _get_db_path_for_project(project_path or "", db_path)
        return _find_symbol_in_db(name, path)
    # If CIL_SQLITE_DB is set, query that single DB
    env_path = os.environ.get("CIL_SQLITE_DB")
    if env_path:
        return _find_symbol_in_db(name, Path(env_path))
    return _scan_all_projects(_find_symbol_in_db)(name)


def _trace_mutations_in_db(target: str, db_path: Path) -> list[dict]:
    """Trace mutations in a single project DB."""
    conn = get_connection(db_path)
    cursor = conn.execute(
        """
        SELECT m.target, m.source, m.line, m.kind, p.project_path
        FROM mutations m
        JOIN projects p ON m.project_id = p.id
        WHERE LOWER(m.target) LIKE LOWER(?)
        """,
        (f"%{target}%",),
    )
    return [dict(row) for row in cursor.fetchall()]


def trace_mutations(target: str, project_path: Optional[str] = None, db_path: Optional[Path] = None) -> list[dict]:
    """Trace all writes to a variable. If project_path is given, only search that project."""
    if project_path or db_path:
        path = _get_db_path_for_project(project_path or "", db_path)
        return _trace_mutations_in_db(target, path)
    # If CIL_SQLITE_DB is set, query that single DB
    env_path = os.environ.get("CIL_SQLITE_DB")
    if env_path:
        return _trace_mutations_in_db(target, Path(env_path))
    return _scan_all_projects(_trace_mutations_in_db)(target)


def _trace_calls_in_db(func_name: str, db_path: Path) -> dict:
    """Trace calls in a single project DB."""
    conn = get_connection(db_path)
    callers = conn.execute(
        """
        SELECT cg.caller, cg.callee, cg.line, p.project_path
        FROM call_graph cg
        JOIN projects p ON cg.project_id = p.id
        WHERE LOWER(cg.caller) LIKE LOWER(?)
        """,
        (f"%{func_name}%",),
    )
    callees = conn.execute(
        """
        SELECT cg.caller, cg.callee, cg.line, p.project_path
        FROM call_graph cg
        JOIN projects p ON cg.project_id = p.id
        WHERE LOWER(cg.callee) LIKE LOWER(?)
        """,
        (f"%{func_name}%",),
    )
    return {
        "callers": [dict(row) for row in callers.fetchall()],
        "callees": [dict(row) for row in callees.fetchall()],
    }


def trace_calls(func_name: str, project_path: Optional[str] = None, db_path: Optional[Path] = None) -> dict:
    """Find callers and callees for a function. If project_path is given, only search that project."""
    if project_path or db_path:
        path = _get_db_path_for_project(project_path or "", db_path)
        return _trace_calls_in_db(func_name, path)
    # If CIL_SQLITE_DB is set, query that single DB
    env_path = os.environ.get("CIL_SQLITE_DB")
    if env_path:
        return _trace_calls_in_db(func_name, Path(env_path))
    return _scan_all_projects(_trace_calls_in_db)(func_name)


def _get_anomalies_in_db(severity: Optional[str] = None, db_path: Path = None) -> list[dict]:
    """Get anomalies from a single project DB."""
    conn = get_connection(db_path)
    if severity:
        cursor = conn.execute(
            """
            SELECT a.type, a.severity, a.file_path, a.line, a.message, p.project_path
            FROM anomalies a
            JOIN projects p ON a.project_id = p.id
            WHERE a.severity = ?
            """,
            (severity,),
        )
    else:
        cursor = conn.execute(
            """
            SELECT a.type, a.severity, a.file_path, a.line, a.message, p.project_path
            FROM anomalies a
            JOIN projects p ON a.project_id = p.id
            """
        )
    return [dict(row) for row in cursor.fetchall()]


def get_anomalies(severity: Optional[str] = None, project_path: Optional[str] = None, db_path: Optional[Path] = None) -> list[dict]:
    """Return all detected anomalies, optionally filtered by severity. If project_path is given, only search that project."""
    if project_path or db_path:
        path = _get_db_path_for_project(project_path or "", db_path)
        return _get_anomalies_in_db(severity, path)
    # If CIL_SQLITE_DB is set, query that single DB
    env_path = os.environ.get("CIL_SQLITE_DB")
    if env_path:
        return _get_anomalies_in_db(severity, Path(env_path))
    return _scan_all_projects(_get_anomalies_in_db)(severity)


def _get_file_summary_in_db(file_path: str, db_path: Path) -> Optional[dict]:
    """Get file summary from a single project DB."""
    conn = get_connection(db_path)
    cursor = conn.execute(
        """
        SELECT f.id, f.file_path, f.file_hash, f.indexed_at
        FROM files f
        WHERE f.file_path = ?
        """,
        (file_path,),
    )
    file_row = cursor.fetchone()
    if not file_row:
        # Try matching by basename
        basename = os.path.basename(file_path)
        cursor = conn.execute(
            """
            SELECT f.id, f.file_path, f.file_hash, f.indexed_at
            FROM files f
            WHERE f.file_path LIKE ?
            """,
            (f"%{basename}",),
        )
        file_row = cursor.fetchone()
        if not file_row:
            return None

    # Get symbols
    sym_cursor = conn.execute(
        """
        SELECT name, kind, line_start, line_end, signature, docstring,
               decorators, purpose, risk_flags, complexity, audit_notes
        FROM symbols WHERE file_id = ?
        """,
        (file_row["id"],),
    )
    symbols = []
    for row in sym_cursor.fetchall():
        symbols.append({
            "name": row["name"],
            "kind": row["kind"],
            "file_path": file_row["file_path"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "signature": row["signature"],
            "docstring": row["docstring"],
            "decorators": json.loads(row["decorators"]) if row["decorators"] else [],
            "purpose": row["purpose"],
            "risk_flags": json.loads(row["risk_flags"]) if row["risk_flags"] else [],
            "complexity": row["complexity"],
            "audit_notes": row["audit_notes"],
        })

    # Get imports
    imp_cursor = conn.execute(
        "SELECT import_path FROM imports WHERE file_id = ?",
        (file_row["id"],),
    )
    imports = [row["import_path"] for row in imp_cursor.fetchall()]

    return {
        "file_path": file_row["file_path"],
        "file_hash": file_row["file_hash"],
        "indexed_at": file_row["indexed_at"],
        "symbols": symbols,
        "imports": imports,
    }


def get_file_summary(file_path: str, project_path: Optional[str] = None, db_path: Optional[Path] = None) -> Optional[dict]:
    """Get file-level summary and symbol list. If project_path is given, only search that project."""
    if project_path or db_path:
        path = _get_db_path_for_project(project_path or "", db_path)
        return _get_file_summary_in_db(file_path, path)
    # If CIL_SQLITE_DB is set, query that single DB
    env_path = os.environ.get("CIL_SQLITE_DB")
    if env_path:
        return _get_file_summary_in_db(file_path, Path(env_path))
    # Scan all project DBs
    for p in list_project_dbs():
        result = _get_file_summary_in_db(file_path, p)
        if result:
            return result
    return None


def get_status(project_path: Optional[str] = None, db_path: Optional[Path] = None) -> list[dict]:
    """Return index freshness and stats for all indexed projects. If project_path given, only that project."""
    if project_path or db_path:
        path = _get_db_path_for_project(project_path or "", db_path)
        conn = get_connection(path)
        cursor = conn.execute(
            """
            SELECT p.project_path, p.indexed_at, p.version,
                   COUNT(DISTINCT f.id) as file_count,
                   COUNT(DISTINCT s.id) as symbol_count
            FROM projects p
            LEFT JOIN files f ON p.id = f.project_id
            LEFT JOIN symbols s ON f.id = s.file_id
            GROUP BY p.id
            """
        )
        return [dict(row) for row in cursor.fetchall()]
    # If CIL_SQLITE_DB is set, query that single DB
    env_path = os.environ.get("CIL_SQLITE_DB")
    if env_path:
        conn = get_connection(Path(env_path))
        cursor = conn.execute(
            """
            SELECT p.project_path, p.indexed_at, p.version,
                   COUNT(DISTINCT f.id) as file_count,
                   COUNT(DISTINCT s.id) as symbol_count
            FROM projects p
            LEFT JOIN files f ON p.id = f.project_id
            LEFT JOIN symbols s ON f.id = s.file_id
            GROUP BY p.id
            """
        )
        return [dict(row) for row in cursor.fetchall()]
    # Scan all project DBs
    results = []
    for p in list_project_dbs():
        conn = get_connection(p)
        cursor = conn.execute(
            """
            SELECT p.project_path, p.indexed_at, p.version,
                   COUNT(DISTINCT f.id) as file_count,
                   COUNT(DISTINCT s.id) as symbol_count
            FROM projects p
            LEFT JOIN files f ON p.id = f.project_id
            LEFT JOIN symbols s ON f.id = s.file_id
            GROUP BY p.id
            """
        )
        results.extend(dict(row) for row in cursor.fetchall())
    return results


def get_file_hash(project_id: int, file_path: str, project_path: str, db_path: Optional[Path] = None) -> Optional[str]:
    """Get the file hash for a given file in a project."""
    path = _get_db_path_for_project(project_path, db_path)
    conn = get_connection(path)
    cursor = conn.execute(
        "SELECT file_hash FROM files WHERE project_id = ? AND file_path = ?",
        (project_id, file_path),
    )
    row = cursor.fetchone()
    return row["file_hash"] if row else None


def get_project_file_hashes(project_id: int, project_path: str, db_path: Optional[Path] = None) -> dict[str, str]:
    """Get all file hashes for a project."""
    path = _get_db_path_for_project(project_path, db_path)
    conn = get_connection(path)
    cursor = conn.execute(
        "SELECT file_path, file_hash FROM files WHERE project_id = ?",
        (project_id,),
    )
    return {row["file_path"]: row["file_hash"] for row in cursor.fetchall()}


def db_status(project_path: Optional[str] = None, db_path: Optional[Path] = None) -> dict:
    """Check SQLite database connectivity."""
    try:
        path = _get_db_path_for_project(project_path or "", db_path)
        conn = get_connection(path)
        conn.execute("SELECT 1")
        conn.close()
        return {"status": "ok", "db_path": str(path)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def store_index(cil_index, db_path: Optional[Path] = None) -> None:
    """Store a CILIndex object into SQLite."""
    from cil.models import CILIndex

    pp = cil_index.project_path
    initialize_db(pp, db_path)
    project_id = upsert_project(pp, cil_index.version, db_path)

    # Clear existing data for this project
    clear_project_data(project_id, pp, db_path)

    # Insert files, symbols, and imports
    for file_path, fi in cil_index.file_indices.items():
        fid = upsert_file(project_id, file_path, fi.file_hash, pp, db_path)
        insert_symbols(fid, [s.model_dump() for s in fi.symbols], pp, db_path)
        insert_imports(fid, fi.imports, pp, db_path)

    # Insert call graph
    insert_call_graph(project_id, [e.model_dump() for e in cil_index.call_graph], pp, db_path)

    # Insert mutations
    insert_mutations(project_id, [m.model_dump() for m in cil_index.mutations], pp, db_path)

    # Insert anomalies
    insert_anomalies(project_id, [a.model_dump() for a in cil_index.anomalies], pp, db_path)


def load_index(project_path: str, db_path: Optional[Path] = None) -> Optional["CILIndex"]:
    """Load a CILIndex from SQLite."""
    from cil.models import CILIndex, FileIndex, SymbolInfo, CallEdge, MutationInfo, Anomaly

    path = _get_db_path_for_project(project_path, db_path)
    project_id = get_project_id(project_path, db_path)
    if not project_id:
        return None

    conn = get_connection(path)

    # Load project info
    proj_cursor = conn.execute(
        "SELECT indexed_at, version FROM projects WHERE id = ?",
        (project_id,),
    )
    proj_row = proj_cursor.fetchone()

    # Load files with symbols and imports
    file_indices = {}
    file_cursor = conn.execute(
        "SELECT id, file_path, file_hash, indexed_at FROM files WHERE project_id = ?",
        (project_id,),
    )
    for frow in file_cursor.fetchall():
        fid = frow["id"]

        # Load symbols
        sym_cursor = conn.execute(
            "SELECT name, kind, line_start, line_end, signature, docstring, decorators, purpose, risk_flags, complexity, audit_notes FROM symbols WHERE file_id = ?",
            (fid,),
        )
        symbols = []
        for srow in sym_cursor.fetchall():
            symbols.append(SymbolInfo(
                name=srow["name"],
                kind=srow["kind"],
                file_path=frow["file_path"],
                line_start=srow["line_start"],
                line_end=srow["line_end"],
                signature=srow["signature"],
                docstring=srow["docstring"],
                decorators=json.loads(srow["decorators"]) if srow["decorators"] else [],
                purpose=srow["purpose"],
                risk_flags=json.loads(srow["risk_flags"]) if srow["risk_flags"] else [],
                complexity=srow["complexity"],
                audit_notes=srow["audit_notes"],
            ))

        # Load imports
        imp_cursor = conn.execute(
            "SELECT import_path FROM imports WHERE file_id = ?",
            (fid,),
        )
        imports = [irow["import_path"] for irow in imp_cursor.fetchall()]

        file_indices[frow["file_path"]] = FileIndex(
            file_path=frow["file_path"],
            symbols=symbols,
            imports=imports,
            indexed_at=datetime.fromisoformat(frow["indexed_at"]),
            file_hash=frow["file_hash"],
        )

    # Load call graph
    call_cursor = conn.execute(
        "SELECT caller, callee, line FROM call_graph WHERE project_id = ?",
        (project_id,),
    )
    call_graph = [CallEdge(caller=r["caller"], callee=r["callee"], line=r["line"]) for r in call_cursor.fetchall()]

    # Load mutations
    mut_cursor = conn.execute(
        "SELECT target, source, line, kind FROM mutations WHERE project_id = ?",
        (project_id,),
    )
    mutations = [MutationInfo(target=r["target"], source=r["source"], line=r["line"], kind=r["kind"]) for r in mut_cursor.fetchall()]

    # Load anomalies
    anom_cursor = conn.execute(
        "SELECT type, severity, file_path, line, message FROM anomalies WHERE project_id = ?",
        (project_id,),
    )
    anomalies = [Anomaly(type=r["type"], severity=r["severity"], file_path=r["file_path"], line=r["line"], message=r["message"]) for r in anom_cursor.fetchall()]

    return CILIndex(
        project_path=project_path,
        file_indices=file_indices,
        call_graph=call_graph,
        mutations=mutations,
        anomalies=anomalies,
        indexed_at=datetime.fromisoformat(proj_row["indexed_at"]),
        version=proj_row["version"],
    )


def migrate_from_mongodb(project_path: Optional[str] = None, db_path: Optional[Path] = None) -> None:
    """Migrate all data from MongoDB to SQLite. If project_path given, only migrate that project."""
    from cil.database import get_collection
    from cil.models import CILIndex

    col = get_collection()
    if project_path:
        docs = col.find({"project_path": project_path}, {"_id": 0})
    else:
        docs = col.find({}, {"_id": 0})

    for doc in docs:
        cil_index = CILIndex(**doc)
        store_index(cil_index, db_path)
        print(f"Migrated: {cil_index.project_path} ({len(cil_index.file_indices)} files)")

    print("Migration complete.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CIL SQLite Database Management")
    subparsers = parser.add_subparsers(dest="command")

    # Init command
    subparsers.add_parser("init", help="Initialize the SQLite database")

    # Migrate command
    subparsers.add_parser("migrate", help="Migrate data from MongoDB to SQLite")

    # Status command
    subparsers.add_parser("status", help="Show database status")

    # Query command
    query_parser = subparsers.add_parser("query", help="Query the SQLite database")
    query_parser.add_argument("symbol", help="Symbol name to find")

    args = parser.parse_args()

    if args.command == "init":
        initialize_db()
        print(f"Database initialized at {get_db_path()}")

    elif args.command == "migrate":
        initialize_db()
        migrate_from_mongodb()

    elif args.command == "status":
        print(json.dumps(db_status(), indent=2))

    elif args.command == "query":
        results = find_symbol(args.symbol)
        print(json.dumps(results, indent=2, default=str))

    else:
        parser.print_help()
