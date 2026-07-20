import logging
import os
import json
from flask import Flask, request, jsonify

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _RATE_LIMITING_AVAILABLE = True
except ImportError:
    _RATE_LIMITING_AVAILABLE = False
    logging.warning("flask-limiter is not installed; API endpoints will have no rate limiting")

from cil.database import get_collection, _sanitize_error
from cil.indexer import Indexer
from cil import sqlite_db

# --- Path redaction (privacy) ---

_HOME = os.path.expanduser("~")


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


def create_app(use_sqlite=True) -> Flask:
    app = Flask(__name__)

    # --- Rate limiting (graceful degradation if flask-limiter unavailable) ---
    limiter = None
    if _RATE_LIMITING_AVAILABLE:
        limiter = Limiter(
            key_func=get_remote_address,
            app=app,
            default_limits=["60 per minute"],
            storage_uri="memory://",
            exclude_host_patterns=[r"^/cil/health$"],
        )

        @limiter.request_filter
        def skip_health():
            return request.path == "/cil/health"

        @app.errorhandler(429)
        def rate_limit_exceeded(e):
            return jsonify({"error": "Rate limit exceeded"}), 429

    _api_key = os.environ.get("CIL_API_KEY")

    # --- CORS policy ---
    _cors_origins_raw = os.environ.get("CIL_CORS_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
    _cors_origins = [o.strip() for o in _cors_origins_raw if o.strip()]

    def _origin_allowed(origin):
        """Check whether *origin* matches one of the configured allowed origins.

        Localhost defaults support wildcard ports (e.g. http://localhost:3000).
        Explicitly configured origins are matched exactly.
        """
        if not origin:
            return False
        for allowed in _cors_origins:
            if origin == allowed:
                return True
            # Wildcard-port matching for localhost / 127.0.0.1
            base = allowed.rstrip(":*")
            if base and origin.startswith(base + ":"):
                return True
        return False

    @app.before_request
    def _check_cors_preflight():
        """Handle CORS preflight OPTIONS requests on /cil/* routes."""
        if request.method != "OPTIONS":
            return None
        if not request.path.startswith("/cil/"):
            return None
        origin = request.headers.get("Origin")
        if not _origin_allowed(origin):
            return jsonify({"error": "CORS policy does not allow that origin"}), 403
        resp = app.make_default_options_response()
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    @app.after_request
    def _apply_cors(response):
        """Attach CORS headers to responses for /cil/* when the Origin is allowed."""
        if not request.path.startswith("/cil/"):
            return response
        origin = request.headers.get("Origin")
        if _origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response

    @app.before_request
    def _check_auth():
        """Bearer-token auth on /cil/* routes when CIL_API_KEY env var is set."""
        if not _api_key:
            return None  # no key configured — skip auth entirely
        if request.path == "/cil/health":
            return None  # health endpoint must stay open for monitoring
        if not request.path.startswith("/cil/"):
            return None  # only protect our own routes
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {_api_key}"
        if auth != expected:
            return jsonify({"error": "Unauthorized"}), 401

    @app.route("/cil/health", methods=["GET"])
    def cil_health():
        """Health check — MongoDB or SQLite connectivity."""
        if use_sqlite:
            try:
                return jsonify({"status": "ok", "backend": "sqlite", "db_path": _redact_path(str(sqlite_db.get_db_path()))})
            except Exception as e:
                return jsonify({"status": "error", "detail": _sanitize_error(e)}), 503
        from cil.database import get_db
        try:
            db = get_db()
            db.command("ping")
            return jsonify({"status": "ok", "backend": "mongodb"})
        except Exception as e:
            return jsonify({"status": "error", "detail": "Database connection failed"}), 503

    @app.route("/cil/status", methods=["GET"])
    def cil_status():
        """Return index freshness and stats."""
        if use_sqlite:
            projects = sqlite_db.get_status()
            return jsonify(_redact_paths_in_result(projects))
        col = get_collection()
        docs = list(col.find({}, {"project_path": 1, "indexed_at": 1, "version": 1, "_id": 0}))

        result = []
        for doc in docs:
            result.append({
                "project_path": _redact_path(doc["project_path"]),
                "indexed_at": doc["indexed_at"].isoformat() if hasattr(doc["indexed_at"], "isoformat") else str(doc["indexed_at"]),
                "version": doc.get("version", 1),
            })

        return jsonify(result)

    @app.route("/cil/index", methods=["POST"])
    @limiter.limit("10 per minute") if limiter else (lambda f: f)
    def cil_index():
        """Trigger re-index of a project path."""
        data = request.get_json()
        if not data or "project_path" not in data:
            return jsonify({"error": "project_path is required"}), 400

        project_path = data["project_path"]
        if not os.path.isdir(project_path):
            return jsonify({"error": f"Directory not found: {_redact_path(project_path)}"}), 404

        enrich = data.get("enrich", False)
        incremental = data.get("incremental", False)

        if use_sqlite:
            return _cil_index_sqlite(project_path, enrich, incremental)

        from cil.models import CILIndex
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

        return jsonify({
            "status": "indexed",
            "project_path": _redact_path(cil_index.project_path),
            "file_count": len(cil_index.file_indices),
            "symbol_count": sum(len(fi.symbols) for fi in cil_index.file_indices.values()),
            "enriched": enrich,
            "incremental": incremental,
        })

    def _cil_index_sqlite(project_path, enrich, incremental):
        from cil.models import CILIndex
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

        return jsonify({
            "status": "indexed",
            "project_path": _redact_path(cil_index.project_path),
            "file_count": len(cil_index.file_indices),
            "symbol_count": sum(len(fi.symbols) for fi in cil_index.file_indices.values()),
            "enriched": enrich,
            "incremental": incremental,
        })

    @app.route("/cil/symbol/<name>", methods=["GET"])
    def cil_symbol(name: str):
        """Find a symbol across all indexed files."""
        if use_sqlite:
            results = sqlite_db.find_symbol(name)
            return jsonify(_redact_paths_in_result(results))
        col = get_collection()
        results = []

        for doc in col.find({}, {"file_indices": 1, "_id": 0}):
            file_indices = doc.get("file_indices", {})
            for fi in file_indices.values():
                for sym in fi.get("symbols", []):
                    if sym.get("name") == name:
                        results.append(sym)

        return jsonify(_redact_paths_in_result(results))

    @app.route("/cil/mutations/<target>", methods=["GET"])
    def cil_mutations(target: str):
        """Trace all writes to a variable."""
        if use_sqlite:
            results = sqlite_db.trace_mutations(target)
            return jsonify(_redact_paths_in_result(results))
        col = get_collection()
        results = []

        for doc in col.find({}, {"mutations": 1, "_id": 0}):
            for m in doc.get("mutations", []):
                if m.get("target") == target:
                    results.append(m)

        return jsonify(_redact_paths_in_result(results))

    @app.route("/cil/calls/<func_name>", methods=["GET"])
    def cil_calls(func_name: str):
        """Find callers and callees for a function."""
        if use_sqlite:
            results = sqlite_db.trace_calls(func_name)
            return jsonify(_redact_paths_in_result(results))
        col = get_collection()
        callers = []
        callees = []

        for doc in col.find({}, {"call_graph": 1, "_id": 0}):
            for edge in doc.get("call_graph", []):
                if func_name in edge.get("caller", ""):
                    callers.append(edge)
                if func_name in edge.get("callee", ""):
                    callees.append(edge)

        return jsonify(_redact_paths_in_result({"callers": callers, "callees": callees}))

    @app.route("/cil/body", methods=["GET"])
    def cil_body():
        """Get raw lines from a file."""
        file_path = request.args.get("file")
        start = int(request.args.get("start", 1))
        end = int(request.args.get("end", 100))

        if not file_path:
            return jsonify({"error": "file parameter is required"}), 400

        redacted_path = _redact_path(file_path)
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            content = "".join(lines[start - 1:end])
            return jsonify({
                "file": redacted_path,
                "start": start,
                "end": end,
                "content": content,
            })
        except FileNotFoundError:
            return jsonify({"error": f"File not found: {redacted_path}"}), 404

    @app.route("/cil/file/<path:path>", methods=["GET"])
    def cil_file(path: str):
        """Get file-level summary and symbol list."""
        redacted_input = _redact_path(path)
        if use_sqlite:
            result = sqlite_db.get_file_summary(path)
            if result is None:
                return jsonify({"error": f"File not found in index: {redacted_input}"}), 404
            return jsonify(_redact_paths_in_result(result))
        col = get_collection()

        for doc in col.find({}, {"file_indices": 1, "_id": 0}):
            file_indices = doc.get("file_indices", {})
            if path in file_indices:
                fi = file_indices[path]
                return jsonify({
                    "file_path": _redact_path(fi.get("file_path", "")),
                    "symbols": _redact_paths_in_result(fi.get("symbols", [])),
                    "imports": fi.get("imports", []),
                    "indexed_at": fi.get("indexed_at"),
                })

        return jsonify({"error": f"File not found in index: {redacted_input}"}), 404

    @app.route("/cil/anomalies", methods=["GET"])
    def cil_anomalies():
        """Return all detected anomalies."""
        severity = request.args.get("severity")
        if use_sqlite:
            results = sqlite_db.get_anomalies(severity=severity)
            return jsonify(_redact_paths_in_result(results))
        col = get_collection()
        results = []

        for doc in col.find({}, {"anomalies": 1, "_id": 0}):
            for a in doc.get("anomalies", []):
                if severity and a.get("severity") != severity:
                    continue
                results.append(a)

        return jsonify(_redact_paths_in_result(results))

    return app
