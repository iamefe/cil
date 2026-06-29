import os
from flask import Flask, request, jsonify

from cil.database import get_collection
from cil.indexer import Indexer
from cil import sqlite_db


def create_app(use_sqlite=False) -> Flask:
    app = Flask(__name__)

    @app.route("/cil/health", methods=["GET"])
    def cil_health():
        """Health check — MongoDB or SQLite connectivity."""
        if use_sqlite:
            try:
                return jsonify({"status": "ok", "backend": "sqlite", "db_path": str(sqlite_db.get_db_path())})
            except Exception as e:
                return jsonify({"status": "error", "detail": str(e)}), 503
        from cil.database import get_db
        try:
            db = get_db()
            db.command("ping")
            return jsonify({"status": "ok", "backend": "mongodb"})
        except Exception as e:
            return jsonify({"status": "error", "detail": str(e)}), 503

    @app.route("/cil/status", methods=["GET"])
    def cil_status():
        """Return index freshness and stats."""
        if use_sqlite:
            projects = sqlite_db.get_status()
            return jsonify(projects)
        col = get_collection()
        docs = list(col.find({}, {"project_path": 1, "indexed_at": 1, "version": 1, "_id": 0}))

        result = []
        for doc in docs:
            result.append({
                "project_path": doc["project_path"],
                "indexed_at": doc["indexed_at"].isoformat() if hasattr(doc["indexed_at"], "isoformat") else str(doc["indexed_at"]),
                "version": doc.get("version", 1),
            })

        return jsonify(result)

    @app.route("/cil/index", methods=["POST"])
    def cil_index():
        """Trigger re-index of a project path."""
        data = request.get_json()
        if not data or "project_path" not in data:
            return jsonify({"error": "project_path is required"}), 400

        project_path = data["project_path"]
        if not os.path.isdir(project_path):
            return jsonify({"error": f"Directory not found: {project_path}"}), 404

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
            "project_path": cil_index.project_path,
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
            "project_path": cil_index.project_path,
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
            return jsonify(results)
        col = get_collection()
        results = []

        for doc in col.find({}, {"file_indices": 1, "_id": 0}):
            file_indices = doc.get("file_indices", {})
            for fi in file_indices.values():
                for sym in fi.get("symbols", []):
                    if sym.get("name") == name:
                        results.append(sym)

        return jsonify(results)

    @app.route("/cil/mutations/<target>", methods=["GET"])
    def cil_mutations(target: str):
        """Trace all writes to a variable."""
        if use_sqlite:
            results = sqlite_db.trace_mutations(target)
            return jsonify(results)
        col = get_collection()
        results = []

        for doc in col.find({}, {"mutations": 1, "_id": 0}):
            for m in doc.get("mutations", []):
                if m.get("target") == target:
                    results.append(m)

        return jsonify(results)

    @app.route("/cil/calls/<func_name>", methods=["GET"])
    def cil_calls(func_name: str):
        """Find callers and callees for a function."""
        if use_sqlite:
            results = sqlite_db.trace_calls(func_name)
            return jsonify(results)
        col = get_collection()
        callers = []
        callees = []

        for doc in col.find({}, {"call_graph": 1, "_id": 0}):
            for edge in doc.get("call_graph", []):
                if func_name in edge.get("caller", ""):
                    callers.append(edge)
                if func_name in edge.get("callee", ""):
                    callees.append(edge)

        return jsonify({"callers": callers, "callees": callees})

    @app.route("/cil/body", methods=["GET"])
    def cil_body():
        """Get raw lines from a file."""
        file_path = request.args.get("file")
        start = int(request.args.get("start", 1))
        end = int(request.args.get("end", 100))

        if not file_path:
            return jsonify({"error": "file parameter is required"}), 400

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            content = "".join(lines[start - 1:end])
            return jsonify({
                "file": file_path,
                "start": start,
                "end": end,
                "content": content,
            })
        except FileNotFoundError:
            return jsonify({"error": f"File not found: {file_path}"}), 404

    @app.route("/cil/file/<path:path>", methods=["GET"])
    def cil_file(path: str):
        """Get file-level summary and symbol list."""
        if use_sqlite:
            result = sqlite_db.get_file_summary(path)
            if result is None:
                return jsonify({"error": f"File not found in index: {path}"}), 404
            return jsonify(result)
        col = get_collection()

        for doc in col.find({}, {"file_indices": 1, "_id": 0}):
            file_indices = doc.get("file_indices", {})
            if path in file_indices:
                fi = file_indices[path]
                return jsonify({
                    "file_path": fi.get("file_path"),
                    "symbols": fi.get("symbols", []),
                    "imports": fi.get("imports", []),
                    "indexed_at": fi.get("indexed_at"),
                })

        return jsonify({"error": f"File not found in index: {path}"}), 404

    @app.route("/cil/anomalies", methods=["GET"])
    def cil_anomalies():
        """Return all detected anomalies."""
        severity = request.args.get("severity")
        if use_sqlite:
            results = sqlite_db.get_anomalies(severity=severity)
            return jsonify(results)
        col = get_collection()
        results = []

        for doc in col.find({}, {"anomalies": 1, "_id": 0}):
            for a in doc.get("anomalies", []):
                if severity and a.get("severity") != severity:
                    continue
                results.append(a)

        return jsonify(results)

    return app
