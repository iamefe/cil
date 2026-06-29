import json
import sys
import time
import os

from cil.database import get_collection, get_db
from cil.indexer import Indexer
from cil import sqlite_db


def create_mcp_server(use_sqlite=False):
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
            db_available_cache["ok"] = False
            db_available_cache["ts"] = now
            db_error_cache["error"] = str(e)
            return False, str(e)

    def _db_error_response(error_msg):
        return {"content": [{"type": "text", "text": f"MongoDB connection failed: {error_msg}"}], "isError": True}

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
            "description": "Find a symbol (function, class, variable) across all indexed files. Returns signature, line range, decorators, and semantic enrichment.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to find (e.g., 'updateStatus', 'Delivery')",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "cil_trace_mutations",
            "description": "Trace all writes to a variable or global state. Returns every location that assigns, augments, or deletes the target.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Variable or state to trace (e.g., '_VISION_READY', 'delivery.status')",
                    },
                },
                "required": ["target"],
            },
        },
        {
            "name": "cil_trace_calls",
            "description": "Find callers and callees for a function. Returns the full call graph up and down.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "func_name": {
                        "type": "string",
                        "description": "Function name to trace (e.g., 'do_swap', 'updateStatus')",
                    },
                },
                "required": ["func_name"],
            },
        },
        {
            "name": "cil_get_anomalies",
            "description": "Return all pre-computed anomaly flags. Filterable by severity.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "description": "Filter by severity (e.g., 'thread unsafe', 'no error handling')",
                    },
                },
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
            "description": "Index a project directory. Stores results in MongoDB or SQLite depending on mode.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the project directory",
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
                "required": ["project_path"],
            },
        },
        {
            "name": "cil_file_summary",
            "description": "Get file-level summary and symbol list from the index.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path (e.g., 'src/cil/indexer/ast_parser.py')",
                    },
                },
                "required": ["path"],
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
            return find_symbol(arguments.get("name", ""))

        if name == "cil_trace_mutations":
            return trace_mutations(arguments.get("target", ""))

        if name == "cil_trace_calls":
            return trace_calls(arguments.get("func_name", ""))

        if name == "cil_get_anomalies":
            return get_anomalies(arguments.get("severity"))

        if name == "cil_get_body":
            return get_body(
                arguments.get("file", ""),
                arguments.get("start", 1),
                arguments.get("end", 100),
            )

        if name == "cil_index_project":
            return index_project(
                arguments.get("project_path", ""),
                arguments.get("enrich", False),
                arguments.get("incremental", False),
            )

        if name == "cil_file_summary":
            return file_summary(arguments.get("path", ""))

        if name == "cil_status":
            return status()

        return {"error": f"Unknown tool: {name}"}

    # --- Tool handlers ---

    def db_status():
        if use_sqlite:
            try:
                db_path = os.environ.get("CIL_SQLITE_DB") or str(sqlite_db.PROJECTS_DIR)
                return {"content": [{"type": "text", "text": json.dumps({"status": "ok", "backend": "sqlite", "db_path": db_path}, indent=2)}]}
            except Exception as e:
                return {"content": [{"type": "text", "text": json.dumps({"status": "error", "detail": str(e)}, indent=2)}], "isError": True}
        ok, err = _db_available()
        if ok:
            return {"content": [{"type": "text", "text": json.dumps({"status": "ok", "backend": "mongodb"}, indent=2)}]}
        return {"content": [{"type": "text", "text": json.dumps({"status": "error", "detail": err}, indent=2)}], "isError": True}

    def find_symbol(name):
        if use_sqlite:
            results = sqlite_db.find_symbol(name)
            return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}
        ok, err = _db_available()
        if not ok:
            return _db_error_response(err)
        col = get_collection()
        results = []
        for doc in col.find({}, {"file_indices": 1, "_id": 0}):
            file_indices = doc.get("file_indices", {})
            for fi in file_indices.values():
                for sym in fi.get("symbols", []):
                    if name.lower() in sym.get("name", "").lower():
                        results.append(sym)
        return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}

    def trace_mutations(target):
        if use_sqlite:
            results = sqlite_db.trace_mutations(target)
            return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}
        ok, err = _db_available()
        if not ok:
            return _db_error_response(err)
        col = get_collection()
        results = []
        for doc in col.find({}, {"mutations": 1, "_id": 0}):
            for m in doc.get("mutations", []):
                if target.lower() in m.get("target", "").lower():
                    results.append(m)
        return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}

    def trace_calls(func_name):
        if use_sqlite:
            results = sqlite_db.trace_calls(func_name)
            return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}
        ok, err = _db_available()
        if not ok:
            return _db_error_response(err)
        col = get_collection()
        callers = []
        callees = []
        for doc in col.find({}, {"call_graph": 1, "_id": 0}):
            for edge in doc.get("call_graph", []):
                if func_name.lower() in edge.get("caller", "").lower():
                    callers.append(edge)
                if func_name.lower() in edge.get("callee", "").lower():
                    callees.append(edge)
        return {"content": [{"type": "text", "text": json.dumps({"callers": callers, "callees": callees}, indent=2, default=str)}]}

    def get_anomalies(severity=None):
        if use_sqlite:
            results = sqlite_db.get_anomalies(severity=severity)
            return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}
        ok, err = _db_available()
        if not ok:
            return _db_error_response(err)
        col = get_collection()
        results = []
        for doc in col.find({}, {"file_indices": 1, "_id": 0}):
            file_indices = doc.get("file_indices", {})
            for fi in file_indices.values():
                for sym in fi.get("symbols", []):
                    risk_flags = sym.get("risk_flags", [])
                    if risk_flags:
                        if severity and severity.lower() not in [f.lower() for f in risk_flags]:
                            continue
                        results.append({
                            "symbol": sym.get("name"),
                            "file_path": sym.get("file_path"),
                            "line_start": sym.get("line_start"),
                            "risk_flags": risk_flags,
                            "audit_notes": sym.get("audit_notes"),
                        })
        return {"content": [{"type": "text", "text": json.dumps(results, indent=2, default=str)}]}

    def get_body(file_path, start=1, end=100):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            content = "".join(lines[start - 1:end])
            return {"content": [{"type": "text", "text": f"{file_path}:{start}-{end}\n{content}"}]}
        except FileNotFoundError:
            return {"content": [{"type": "text", "text": f"File not found: {file_path}"}], "isError": True}

    def index_project(project_path, enrich=False, incremental=False):
        from cil.models import CILIndex
        if not os.path.isdir(project_path):
            return {"content": [{"type": "text", "text": f"Directory not found: {project_path}"}], "isError": True}

        if use_sqlite:
            return _index_project_sqlite(project_path, enrich, incremental)

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
            "project_path": cil_index.project_path,
            "file_count": len(cil_index.file_indices),
            "symbol_count": sum(len(fi.symbols) for fi in cil_index.file_indices.values()),
            "enriched": enrich,
            "incremental": incremental,
        }, indent=2)}]}

    def _index_project_sqlite(project_path, enrich, incremental):
        previous_index = None
        if incremental:
            previous_index = sqlite_db.load_index(project_path)

        indexer = Indexer()
        cil_index = indexer.index_directory(
            project_path,
            enrich=enrich,
            incremental=incremental,
            previous_index=previous_index,
        )

        sqlite_db.store_index(cil_index)

        return {"content": [{"type": "text", "text": json.dumps({
            "status": "indexed",
            "project_path": cil_index.project_path,
            "file_count": len(cil_index.file_indices),
            "symbol_count": sum(len(fi.symbols) for fi in cil_index.file_indices.values()),
            "enriched": enrich,
            "incremental": incremental,
        }, indent=2)}]}

    def file_summary(path):
        if use_sqlite:
            result = sqlite_db.get_file_summary(path)
            if result is None:
                return {"content": [{"type": "text", "text": f"File not found in index: {path}"}], "isError": True}
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}
        ok, err = _db_available()
        if not ok:
            return _db_error_response(err)
        col = get_collection()
        for doc in col.find({}, {"file_indices": 1, "_id": 0}):
            file_indices = doc.get("file_indices", {})
            if path in file_indices:
                fi = file_indices[path]
                return {"content": [{"type": "text", "text": json.dumps({
                    "file_path": fi.get("file_path"),
                    "symbols": fi.get("symbols", []),
                    "imports": fi.get("imports", []),
                }, indent=2, default=str)}]}
        return {"content": [{"type": "text", "text": f"File not found in index: {path}"}], "isError": True}

    def status():
        if use_sqlite:
            projects = sqlite_db.get_status()
            return {"content": [{"type": "text", "text": json.dumps(projects, indent=2, default=str)}]}
        ok, err = _db_available()
        if not ok:
            return _db_error_response(err)
        col = get_collection()
        docs = list(col.find({}, {"project_path": 1, "indexed_at": 1, "version": 1, "_id": 0}))
        result = []
        for doc in docs:
            result.append({
                "project_path": doc.get("project_path"),
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
                send_error(str(e), req_id)

    return run
