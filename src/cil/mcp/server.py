import json
import sys
import time
import os
from pathlib import Path

from cil.database import get_collection, get_db, _sanitize_error
from cil.indexer import Indexer
from cil import sqlite_db

# --- Path redaction (privacy) ---

_HOME = os.path.expanduser("~")


def _norm(p: str) -> Path:
    """Normalize a user-supplied path: expand ~ and resolve symlinks."""
    return Path(p).expanduser().resolve(strict=False)


def _redact_path(filepath):
    """Convert an absolute filesystem path to a privacy-safe relative form.

    - If filepath starts with $HOME, replace $HOME with ~
    - Otherwise, keep just the last 3 path components as fallback
    """
    if not isinstance(filepath, str):
        return filepath
    real = os.path.realpath(filepath)
    home_real = os.path.realpath(_HOME)
    if real.startswith(home_real + os.sep) or real == home_real:
        return "~" + real[len(home_real):]
    # Fallback: last 3 components (preserve leading / for absolute paths)
    parts = filepath.rstrip(os.sep).split(os.sep)
    joined = os.sep.join(parts[-3:])
    return os.sep + joined if filepath.startswith(os.sep) and len(parts) > 3 else filepath


def _redact_paths_in_result(obj):
    """Recursively redact known path-containing keys in dicts/lists."""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k in ("file_path", "project_path", "db_path"):
                result[k] = _redact_path(v)
            elif k in ("caller", "source", "callee"):
                # These are "path:line:name" — redact only the path portion
                if isinstance(v, str) and ":" in v:
                    first_colon = v.index(":")
                    result[k] = _redact_path(v[:first_colon]) + v[first_colon:]
                else:
                    result[k] = _redact_paths_in_result(v)
            else:
                result[k] = _redact_paths_in_result(v)
        return result
    if isinstance(obj, list):
        return [_redact_paths_in_result(item) for item in obj]
    return obj


# --- Path allowlist security ---

def _get_allowed_dirs():
    """Return list of allowed root directories.

    Read from CIL_ALLOWED_DIRS (comma-separated). If not set, default to $HOME.
    """
    env_val = os.environ.get("CIL_ALLOWED_DIRS", "")
    if env_val:
        dirs = [d.strip() for d in env_val.split(",") if d.strip()]
        # Resolve each directory to its real path
        return [str(_norm(d)) for d in dirs]
    home = os.environ.get("HOME", "/tmp")
    return [str(_norm(home))]

ALLOWED_DIRS = _get_allowed_dirs()


def _is_path_allowed(path):
    """Check that a filesystem path is within the allowed directories.

    - Rejects paths containing '..' traversal sequences before resolution
    - Resolves with os.path.realpath() and checks against allowlist
    Returns (True, None) on success or (False, error_message) on rejection.
    """
    # Pre-resolution check: reject obvious traversal attempts
    if ".." in path:
        return False, f"Path contains traversal sequence '..': {path}"

    resolved = str(_norm(path))

    for allowed in ALLOWED_DIRS:
        # Ensure trailing slash for proper prefix matching
        # e.g., /home/user must not match /home/user_evil
        if resolved == allowed or resolved.startswith(allowed + os.sep):
            return True, None

    return False, (
        f"Path outside allowed directories: {path}. "
        f"Allowed dirs: {', '.join(ALLOWED_DIRS)}"
    )


def create_mcp_server(use_sqlite=True):
    """Create an MCP server that wraps CIL endpoints as tools."""

    db_available_cache = {"ok": None, "ts": 0}
    db_error_cache = {"error": None, "ts": 0}
    CACHE_TTL = 30

    def _db_available():
        """Check MongoDB connectivity with short TTL cache."""
        now = time.time()
        if db_available_cache["ts"] + CACHE_TTL > now:
            return db_available_cache["ok"], db_error_cache["error"]
        try:
            get_db().command("ping")
            db_available_cache["ok"] = True
            db_available_cache["ts"] = now
            db_error_cache["error"] = None
            return True, None
        except Exception as e:
            sanitized = _sanitize_error(e)
            db_available_cache["ok"] = False
            db_available_cache["ts"] = now
            db_error_cache["error"] = sanitized
            return False, sanitized

    def _db_error_response(error_msg):
        return {"content": [{"type": "text", "text": f"MongoDB connection failed: {error_msg}"}], "isError": True}

    def _project_error_response(msg):
        return {"content": [{"type": "text", "text": msg}], "isError": True}

    def _resolve_project(project):
        """Resolve the required 'project' argument to the indexed project identity.

        Accepts a full indexed path or a bare project name (directory stem).
        Returns (resolved, error): resolved is the project path string to use,
        error is an MCP error response (or None if resolution succeeded).
        """
        if not project:
            return None, _project_error_response("Error: 'project' argument is required. Use cil_status to list indexed projects.")
        if use_sqlite:
            if sqlite_db.resolve_project(project) is None:
                available = ", ".join(sqlite_db.list_project_names())
                return None, _project_error_response(f"Error: project '{project}' is not indexed. Available: {available}")
            return project, None
        ok, err = _db_available()
        if not ok:
            return None, _db_error_response(err)
        col = get_collection()
        target = str(_norm(project))
        if col.count_documents({"project_path": target}) > 0:
            return target, None
        stems = {}
        for d in col.find({}, {"project_path": 1, "_id": 0}):
            pp = d.get("project_path")
            if pp:
                stems[Path(pp).stem] = pp
        if Path(target).stem in stems:
            return stems[Path(target).stem], None
        available = ", ".join(sorted(stems.values()))
        return None, _project_error_response(f"Error: project '{project}' is not indexed. Available: {available}")

    tools = [
        {
            "name": "cil_db_status",
            "description": "Check database connectivity status (MongoDB or SQLite).",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "cil_find_symbol",
            "description": "Find a symbol (function, class, variable) within a project. Returns signature, line range, decorators, and semantic enrichment.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to find (e.g., 'updateStatus', 'Delivery')",
                    },
                    "project": {
                        "type": "string",
                        "description": "Project to query: indexed project name (e.g., 'nibia') or full indexed path. Required. Use cil_status to list projects.",
                    },
                },
                "required": ["name", "project"],
            },
        },
        {
            "name": "cil_trace_mutations",
            "description": "Trace all writes to a variable or global state within a project. Returns every location that assigns, augments, or deletes the target.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Variable or state to trace (e.g., '_VISION_READY', 'delivery.status')",
                    },
                    "project": {
                        "type": "string",
                        "description": "Project to query: indexed project name (e.g., 'nibia') or full indexed path. Required. Use cil_status to list projects.",
                    },
                },
                "required": ["target", "project"],
            },
        },
        {
            "name": "cil_trace_calls",
            "description": "Find callers and callees for a function within a project. Returns the full call graph up and down.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "func_name": {
                        "type": "string",
                        "description": "Function name to trace (e.g., 'do_swap', 'updateStatus')",
                    },
                    "project": {
                        "type": "string",
                        "description": "Project to query: indexed project name (e.g., 'nibia') or full indexed path. Required. Use cil_status to list projects.",
                    },
                },
                "required": ["func_name", "project"],
            },
        },
        {
            "name": "cil_get_anomalies",
            "description": "Return all pre-computed anomaly flags for a project. Filterable by severity.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project to query: indexed project name (e.g., 'nibia') or full indexed path. Required. Use cil_status to list projects.",
                    },
                    "severity": {
                        "type": "string",
                        "description": "Filter by severity (e.g., 'thread unsafe', 'no error handling')",
                    },
                },
                "required": ["project"],
            },
        },
        {
            "name": "cil_get_body",
            "description": "Get raw lines from a file. Use only when symbol info is insufficient.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "File path",
                    },
                    "start": {
                        "type": "integer",
                        "description": "Start line (1-indexed)",
                        "default": 1,
                    },
                    "end": {
                        "type": "integer",
                        "description": "End line",
                        "default": 100,
                    },
                },
                "required": ["file"],
            },
        },
        {
            "name": "cil_index_project",
            "description": "Index a project directory. SQLite mode stores results in the per-project DB named `name` (mandatory); MongoDB mode keys by project_path and ignores `name`.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project directory",
                    },
                    "name": {
                        "type": "string",
                        "description": "Mandatory unique DB name (e.g., 'nibia-admin'). Must not be used by another indexed project. Ignored in MongoDB mode.",
                    },
                    "enrich": {
                        "type": "boolean",
                        "description": "Run LLM semantic enrichment (purpose, complexity, audit notes)",
                        "default": False,
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "Only re-index changed files (requires existing index)",
                        "default": False,
                    },
                },
                "required": ["project_path", "name"],
            },
        },
        {
            "name": "cil_file_summary",
            "description": "Get file-level summary and symbol list from the index for a project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path (absolute, or relative to the project root)",
                    },
                    "project": {
                        "type": "string",
                        "description": "Project to query: indexed project name (e.g., 'nibia') or full indexed path. Required. Use cil_status to list projects.",
                    },
                },
                "required": ["path", "project"],
            },
        },
        {
            "name": "cil_status",
            "description": "Return index freshness and stats for all indexed projects.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]

    def handle_request(request):
        method = request.get("method")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "cil",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "tools": {},
                },
            }

        if method == "tools/list":
            return {"tools": tools}

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            return handle_tool_call(tool_name, arguments)

        return {}

    def handle_tool_call(name, arguments):
        if name == "cil_db_status":
            return db_status()

        if name == "cil_find_symbol":
            return find_symbol(arguments.get("name", ""), arguments.get("project", ""))

        if name == "cil_trace_mutations":
            return trace_mutations(arguments.get("target", ""), arguments.get("project", ""))

        if name == "cil_trace_calls":
            return trace_calls(arguments.get("func_name", ""), arguments.get("project", ""))

        if name == "cil_get_anomalies":
            return get_anomalies(arguments.get("project", ""), arguments.get("severity"))

        if name == "cil_get_body":
            return get_body(
                arguments.get("file", ""),
                arguments.get("start", 1),
                arguments.get("end", 100),
            )

        if name == "cil_index_project":
            return index_project(
                arguments.get("project_path", ""),
                arguments.get("name", ""),
                arguments.get("enrich", False),
                arguments.get("incremental", False),
            )

        if name == "cil_file_summary":
            return file_summary(arguments.get("path", ""), arguments.get("project", ""))

        if name == "cil_status":
            return status()

        return {"error": f"Unknown tool: {name}"}

    # --- Tool handlers ---

    def db_status():
        if use_sqlite:
            try:
                db_path = str(sqlite_db.env_db_path() or sqlite_db.PROJECTS_DIR)
                return {"content": [{"type": "text", "text": json.dumps({"status": "ok", "backend": "sqlite", "db_path": _redact_path(db_path)}, indent=2)}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": json.dumps({"status": "error", "detail": str(e)}, indent=2)}], "isError": True}
        ok, err = _db_available()
        if ok:
            return {"content": [{"type": "text", "text": json.dumps({"status": "ok", "backend": "mongodb"}, indent=2)}]}
        return {"content": [{"type": "text", "text": json.dumps({"status": "error", "detail": err}, indent=2)}], "isError": True}

    def find_symbol(name, project=""):
        resolved, err = _resolve_project(project)
        if err:
            return err
        if use_sqlite:
            results = sqlite_db.find_symbol(name, project_path=resolved)
            results = _redact_paths_in_result(results)
            return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}
        col = get_collection()
        doc = col.find_one({"project_path": resolved}, {"file_indices": 1, "_id": 0})
        results = []
        if doc:
            for fi in doc.get("file_indices", {}).values():
                for sym in fi.get("symbols", []):
                    if name.lower() in sym.get("name", "").lower():
                        results.append(sym)
        results = _redact_paths_in_result(results)
        return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}

    def trace_mutations(target, project=""):
        resolved, err = _resolve_project(project)
        if err:
            return err
        if use_sqlite:
            results = sqlite_db.trace_mutations(target, project_path=resolved)
            results = _redact_paths_in_result(results)
            return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}
        col = get_collection()
        doc = col.find_one({"project_path": resolved}, {"mutations": 1, "_id": 0})
        results = []
        if doc:
            for m in doc.get("mutations", []):
                if target.lower() in m.get("target", "").lower():
                    results.append(m)
        results = _redact_paths_in_result(results)
        return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}

    def trace_calls(func_name, project=""):
        resolved, err = _resolve_project(project)
        if err:
            return err
        if use_sqlite:
            results = sqlite_db.trace_calls(func_name, project_path=resolved)
            results = _redact_paths_in_result(results)
            return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}
        col = get_collection()
        doc = col.find_one({"project_path": resolved}, {"call_graph": 1, "_id": 0})
        callers = []
        callees = []
        if doc:
            for edge in doc.get("call_graph", []):
                if func_name.lower() in edge.get("caller", "").lower():
                    callers.append(edge)
                if func_name.lower() in edge.get("callee", "").lower():
                    callees.append(edge)
        return {"content": [{"type": "text", "text": json.dumps(_redact_paths_in_result({"callers": callers, "callees": callees}), indent=2, default=str)}]}

    def get_anomalies(project="", severity=None):
        resolved, err = _resolve_project(project)
        if err:
            return err
        if use_sqlite:
            results = sqlite_db.get_anomalies(severity=severity, project_path=resolved)
            results = _redact_paths_in_result(results)
            return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}
        col = get_collection()
        doc = col.find_one({"project_path": resolved}, {"file_indices": 1, "_id": 0})
        results = []
        if doc:
            for fi in doc.get("file_indices", {}).values():
                for sym in fi.get("symbols", []):
                    risk_flags = sym.get("risk_flags", [])
                    if risk_flags:
                        if severity and severity.lower() not in [f.lower() for f in risk_flags]:
                            continue
                        results.append({
                            "symbol": sym.get("name"),
                            "file_path": _redact_path(sym.get("file_path", "")),
                            "line_start": sym.get("line_start"),
                            "risk_flags": risk_flags,
                            "audit_notes": sym.get("audit_notes"),
                        })
        return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}

    def get_body(file_path, start=1, end=100):
        # Security: validate path is within allowed directories
        allowed, err_msg = _is_path_allowed(file_path)
        if not allowed:
            return {"content": [{"type": "text", "text": err_msg}], "isError": True}

        redacted_path = _redact_path(file_path)
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            content = "".join(lines[start - 1:end])
            return {"content": [{"type": "text", "text": f"{redacted_path}:{start}-{end}\n{content}"}]}
        except FileNotFoundError:
            return {"content": [{"type": "text", "text": f"File not found: {redacted_path}"}], "isError": True}

    def index_project(project_path, name, enrich=False, incremental=False):
        from cil.models import CILIndex
        # Security: validate path is within allowed directories before any file reading
        allowed, err_msg = _is_path_allowed(project_path)
        if not allowed:
            return {"content": [{"type": "text", "text": err_msg}], "isError": True}

        if not os.path.isdir(project_path):
            return {"content": [{"type": "text", "text": f"Directory not found: {_redact_path(project_path)}"}], "isError": True}

        if use_sqlite:
            if not sqlite_db.env_db_path():
                err = sqlite_db.validate_project_name(name) or sqlite_db.claim_project_name(name, project_path)
                if err:
                    return {"content": [{"type": "text", "text": f"Error: {err} Available names: {', '.join(sqlite_db.list_project_names()) or '(none)'}"}], "isError": True}
            return _index_project_sqlite(project_path, name, enrich, incremental)

        ok, err = _db_available()
        if not ok:
            return _db_error_response(err)

        previous_index = None
        if incremental:
            col = get_collection()
            doc = col.find_one({"project_path": project_path})
            if doc:
                previous_index = CILIndex(**doc)

        indexer = Indexer()
        cil_index = indexer.index_directory(
            project_path,
            enrich=enrich,
            incremental=incremental,
            previous_index=previous_index,
        )

        col = get_collection()
        col.replace_one(
            {"project_path": cil_index.project_path},
            cil_index.model_dump(),
            upsert=True,
        )

        return {"content": [{"type": "text", "text": json.dumps({
            "status": "indexed",
            "project_path": _redact_path(cil_index.project_path),
            "file_count": len(cil_index.file_indices),
            "symbol_count": sum(len(fi.symbols) for fi in cil_index.file_indices.values()),
            "enriched": enrich,
            "incremental": incremental,
        }, indent=2)}]}

    def _index_project_sqlite(project_path, name, enrich, incremental):
        db_path = None
        if not sqlite_db.env_db_path():
            db_path = sqlite_db.get_project_db_path(name)

        previous_index = None
        if incremental:
            previous_index = sqlite_db.load_index(project_path, db_path)

        indexer = Indexer()
        cil_index = indexer.index_directory(
            project_path,
            enrich=enrich,
            incremental=incremental,
            previous_index=previous_index,
        )

        sqlite_db.store_index(cil_index, name, db_path)

        return {"content": [{"type": "text", "text": json.dumps({
            "status": "indexed",
            "name": name if not sqlite_db.env_db_path() else None,
            "project_path": _redact_path(cil_index.project_path),
            "file_count": len(cil_index.file_indices),
            "symbol_count": sum(len(fi.symbols) for fi in cil_index.file_indices.values()),
            "enriched": enrich,
            "incremental": incremental,
        }, indent=2)}]}

    def file_summary(path, project=""):
        # Security: validate path is within allowed directories
        allowed, err_msg = _is_path_allowed(path)
        if not allowed:
            return {"content": [{"type": "text", "text": err_msg}], "isError": True}

        redacted_input = _redact_path(path)
        resolved, err = _resolve_project(project)
        if err:
            return err
        if use_sqlite:
            result = sqlite_db.get_file_summary(path, project_path=resolved)
            if result is None:
                return {"content": [{"type": "text", "text": f"File not found in index: {redacted_input}"}], "isError": True}
            result = _redact_paths_in_result(result)
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}
        col = get_collection()
        doc = col.find_one({"project_path": resolved}, {"file_indices": 1, "_id": 0})
        if doc:
            file_indices = doc.get("file_indices", {})
            if path in file_indices:
                fi = file_indices[path]
                return {"content": [{"type": "text", "text": json.dumps({
                    "file_path": _redact_path(fi.get("file_path", "")),
                    "symbols": _redact_paths_in_result(fi.get("symbols", [])),
                    "imports": fi.get("imports", []),
                }, indent=2, default=str)}]}
        return {"content": [{"type": "text", "text": f"File not found in index: {redacted_input}"}], "isError": True}

    def status():
        if use_sqlite:
            projects = sqlite_db.get_status()
            projects = _redact_paths_in_result(projects)
            return {"content": [{"type": "text", "text": json.dumps(projects, indent=2, default=str)}]}
        ok, err = _db_available()
        if not ok:
            return _db_error_response(err)
        col = get_collection()
        docs = list(col.find({}, {"project_path": 1, "indexed_at": 1, "version": 1, "_id": 0}))
        result = []
        for doc in docs:
            result.append({
                "project_path": _redact_path(doc.get("project_path", "")),
                "indexed_at": doc.get("indexed_at"),
                "version": doc.get("version", 1),
            })
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}

    # --- MCP stdio transport ---

    def run():
        """Run the MCP server over stdio."""
        request_id = 0

        def send_response(result, req_id):
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result,
            }
            sys.stdout.write(json.dumps(msg) + "\n")
            sys.stdout.flush()

        def send_error(message, req_id, code=-32000):
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": code,
                    "message": message,
                },
            }
            sys.stdout.write(json.dumps(msg) + "\n")
            sys.stdout.flush()

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue

            req_id = req.get("id", 0)
            method = req.get("method")

            if not method:
                continue

            try:
                result = handle_request(req)
                send_response(result, req_id)
            except Exception as e:
                send_error(_sanitize_error(e), req_id)

    return run
