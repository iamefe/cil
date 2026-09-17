import argparse
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cil.indexer import Indexer
from cil.database import get_collection, ensure_indexes
from cil import sqlite_db


def _norm(p):
    """Normalize a user-supplied path: expand ~ and resolve symlinks. Passes non-strings through."""
    from pathlib import Path
    if p is None:
        return None
    if not isinstance(p, str):
        return p
    return Path(p).expanduser().resolve(strict=False)


def main():
    parser = argparse.ArgumentParser(description="Code Intelligence Layer (CIL)")
    subparsers = parser.add_subparsers(dest="command")

    # Index command
    index_parser = subparsers.add_parser("index", help="Index a project")
    index_parser.add_argument("project_path", help="Path to the project directory")
    index_parser.add_argument("--name",
        help="Mandatory (SQLite mode) unique DB name (e.g. 'nibia-admin'). Must not be used by another indexed project. Ignored with --mongo.")
    index_parser.add_argument("--enrich", action="store_true", help="Run LLM semantic enrichment")
    index_parser.add_argument("--force", action="store_true", help="Clear old index before re-indexing")
    index_parser.add_argument("--incremental", action="store_true", help="Only re-index changed files")
    index_parser.add_argument("--mongo", action="store_true", help="Use MongoDB instead of SQLite")

    # Status command
    status_parser = subparsers.add_parser("status", help="Show index status")
    status_parser.add_argument("--mongo", action="store_true", help="Use MongoDB instead of SQLite")

    # Query command
    query_parser = subparsers.add_parser("query", help="Query the index")
    query_parser.add_argument("symbol", help="Symbol name to find")
    query_parser.add_argument("--project", required=True, help="Project path or name to query (required). Use 'cil status' to list projects.")
    query_parser.add_argument("--mongo", action="store_true", help="Use MongoDB instead of SQLite")

    # MCP server command
    serve_parser = subparsers.add_parser("serve", help="Start MCP server")
    serve_parser.add_argument("--mongo", action="store_true", help="Use MongoDB instead of SQLite")

    # Anomalies command
    anom_parser = subparsers.add_parser("anomalies", help="List detected anomalies (Python files only)")
    anom_parser.add_argument("--project", required=True, help="Project path or name to query (required). Use 'cil status' to list projects.")
    anom_parser.add_argument("--severity", choices=["low", "medium", "high"], help="Filter by severity")
    anom_parser.add_argument("--file", help="Filter by file path")
    anom_parser.add_argument("--mongo", action="store_true", help="Use MongoDB instead of SQLite")

    # Watch command
    watch_parser = subparsers.add_parser("watch", help="Watch directory for changes and auto-reindex")
    watch_parser.add_argument("project_path", help="Path to the project directory")
    watch_parser.add_argument("--enrich", action="store_true", help="Run LLM semantic enrichment on re-index")
    watch_parser.add_argument("--mongo", action="store_true", help="Use MongoDB instead of SQLite")

    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a project from SQLite")
    remove_parser.add_argument("project_path", help="Path to the project directory")

    # Enrich command
    enrich_parser = subparsers.add_parser("enrich", help="Run LLM semantic enrichment on existing index")
    enrich_parser.add_argument("--mongo", action="store_true", help="Use MongoDB instead of SQLite")

    # SQLite-specific commands
    sqlite_parser = subparsers.add_parser("sqlite", help="SQLite database management")
    sqlite_sub = sqlite_parser.add_subparsers(dest="sqlite_command")
    sqlite_init = sqlite_sub.add_parser("init", help="Initialize SQLite database")
    sqlite_init.add_argument("--name", required=True, help="Custom project DB name")
    sqlite_sub.add_parser("migrate", help="Migrate data from MongoDB to SQLite")
    sqlite_query = sqlite_sub.add_parser("query", help="Query SQLite database")
    sqlite_query.add_argument("symbol", help="Symbol name to find")
    sqlite_query.add_argument("--project", required=True, help="Project path or name to query (required)")
    sqlite_remove = sqlite_sub.add_parser("remove", help="Remove a project from SQLite")
    sqlite_remove.add_argument("project_path", help="Path to the project directory")
    sqlite_sub.add_parser("prune", help="Remove invalid paths from watch database")
    sqlite_prune_index = sqlite_sub.add_parser("prune-index", help="Permanently delete inactive rows from index tables")
    sqlite_prune_index.add_argument("project_path", help="Path to the project directory")

    # Watch-all: watch all registered paths from the watch database
    watch_all_parser = subparsers.add_parser("watch-all", help="Watch all registered paths from watch database")
    watch_all_parser.add_argument("--enrich", action="store_true", help="Enable LLM semantic enrichment")
    watch_all_parser.add_argument("--mongo", action="store_true", help="Use MongoDB instead of SQLite")

    args = parser.parse_args()

    # Ensure MongoDB indexes exist (skip if using SQLite-only commands)
    if args.command != "sqlite":
        try:
            ensure_indexes()
        except Exception:
            pass  # Non-critical if DB is down

    if args.command == "index":
        if not args.mongo:
            _index_sqlite(args)
        else:
            _index_mongodb(args)

    elif args.command == "status":
        if not args.mongo:
            _status_sqlite()
        else:
            _status_mongodb()

    elif args.command == "query":
        if not args.mongo:
            _query_sqlite(args)
        else:
            _query_mongodb(args)

    elif args.command == "serve":
        from cil.mcp.server import create_mcp_server
        server = create_mcp_server(use_sqlite=not args.mongo)
        server()

    elif args.command == "anomalies":
        if not args.mongo:
            _anomalies_sqlite(args)
        else:
            _anomalies_mongodb(args)

    elif args.command == "watch":
        project_path = str(_norm(args.project_path))

        # Validate path exists
        if not os.path.exists(project_path):
            print(f"Error: Path does not exist: {project_path}")
            sys.exit(1)

        # Register and validate in watch database
        sqlite_db.register_watched_path(project_path)

        from cil.watcher import FileWatcher
        watcher = FileWatcher(project_path, enrich=args.enrich, use_sqlite=not args.mongo)
        watcher.start()

    elif args.command == "watch-all":
        invalid = sqlite_db.validate_all_paths()
        if invalid:
            print(f"Warning: {len(invalid)} invalid path(s) found:")
            for p in invalid:
                print(f"  - {p}")
            print("Run 'cil sqlite prune' to remove invalid paths.\n")

        valid_paths = sqlite_db.get_valid_paths()
        if not valid_paths:
            print("No valid paths to watch. Register paths with 'cil watch <path>'.")
            sys.exit(1)

        print(f"Watching {len(valid_paths)} path(s):\n")
        for p in valid_paths:
            print(f"  - {p}")
        print()

        from cil.watcher import FileWatcher
        import threading

        watchers = []
        for path in valid_paths:
            watcher = FileWatcher(path, enrich=args.enrich, use_sqlite=not args.mongo)
            t = threading.Thread(target=watcher.start, daemon=True)
            t.start()
            watchers.append((watcher, t))

        try:
            for watcher, t in watchers:
                t.join()
        except KeyboardInterrupt:
            for watcher, _ in watchers:
                watcher.stop()

    elif args.command == "remove":
        sqlite_db.remove_project(args.project_path)
        print(f"Removed project: {args.project_path}")

    elif args.command == "enrich":
        if not args.mongo:
            _enrich_sqlite()
        else:
            _enrich_mongodb()

    elif args.command == "sqlite":
        _sqlite_command(args)


def _index_sqlite(args):
    """Index a project using SQLite storage."""
    from cil.models import CILIndex

    project_path = str(_norm(args.project_path))
    name = args.name

    # Resolve target DB: legacy single-DB (env) or per-project (mandatory custom name)
    _env_db = sqlite_db.env_db_path()
    if _env_db:
        db_path = _env_db
    else:
        if not name:
            print("Error: --name is required in SQLite mode (unique DB name, e.g. 'nibia-admin').")
            print("Indexed projects: " + (", ".join(sqlite_db.list_project_names()) or "(none)"))
            sys.exit(1)
        err = sqlite_db.validate_project_name(name) or sqlite_db.claim_project_name(name, project_path)
        if err:
            print(f"Error: {err}")
            print("Indexed projects: " + (", ".join(sqlite_db.list_project_names()) or "(none)"))
            sys.exit(1)
        db_path = sqlite_db.get_project_db_path(name)

    # --force: clear old index
    if args.force:
        sqlite_db.delete_project(project_path, db_path)

    # Load previous index for incremental mode
    previous_index = None
    if args.incremental:
        previous_index = sqlite_db.load_index(project_path, db_path)
        if not previous_index:
            print("No previous index found, doing full index")
            args.incremental = False

    indexer = Indexer()
    cil_index = indexer.index_directory(
        project_path,
        enrich=args.enrich,
        incremental=args.incremental,
        previous_index=previous_index,
    )

    sqlite_db.store_index(cil_index, name, db_path)
    sqlite_db.register_watched_path(project_path)

    print(f"Indexed {name} -> {cil_index.project_path}")
    print(f"  Files: {len(cil_index.file_indices)}")
    print(f"  Symbols: {sum(len(fi.symbols) for fi in cil_index.file_indices.values())}")
    print(f"  Call edges: {len(cil_index.call_graph)}")
    print(f"  Mutations: {len(cil_index.mutations)}")
    print(f"  Anomalies: {len(cil_index.anomalies)}")


def _index_mongodb(args):
    """Index a project using MongoDB storage."""
    from cil.models import CILIndex

    col = get_collection()

    # --force: clear old index before re-indexing
    if args.force:
        col.delete_one({"project_path": args.project_path})

    # Load previous index for incremental mode
    previous_index = None
    if args.incremental:
        doc = col.find_one({"project_path": args.project_path})
        if doc:
            previous_index = CILIndex(**doc)
        else:
            print("No previous index found, doing full index")
            args.incremental = False

    indexer = Indexer()
    cil_index = indexer.index_directory(
        args.project_path,
        enrich=args.enrich,
        incremental=args.incremental,
        previous_index=previous_index,
    )

    col.replace_one(
        {"project_path": cil_index.project_path},
        cil_index.model_dump(),
        upsert=True,
    )

    print(f"Indexed {cil_index.project_path}")
    print(f"  Files: {len(cil_index.file_indices)}")
    print(f"  Symbols: {sum(len(fi.symbols) for fi in cil_index.file_indices.values())}")
    print(f"  Call edges: {len(cil_index.call_graph)}")
    print(f"  Mutations: {len(cil_index.mutations)}")
    print(f"  Anomalies: {len(cil_index.anomalies)}")


def _status_sqlite():
    """Show index status from SQLite."""
    projects = sqlite_db.get_status()
    if not projects:
        print("No indexed projects")
    for p in projects:
        print(f"  {p['project_path']} (v{p['version']}) — {p['indexed_at']} ({p['file_count']} files, {p['symbol_count']} symbols)")


def _status_mongodb():
    """Show index status from MongoDB."""
    col = get_collection()
    docs = list(col.find({}, {"project_path": 1, "indexed_at": 1, "version": 1, "_id": 0}))
    if not docs:
        print("No indexed projects")
    for doc in docs:
        print(f"  {doc['project_path']} (v{doc.get('version', '?')}) — {doc.get('indexed_at', '?')}")


def _resolve_project_mongo(project):
    """Resolve a project path/name to the indexed project_path in MongoDB."""
    col = get_collection()
    target = str(_norm(project))
    if col.count_documents({"project_path": target}) > 0:
        return target
    stems = {}
    for d in col.find({}, {"project_path": 1, "_id": 0}):
        pp = d.get("project_path")
        if pp:
            stems[Path(pp).stem] = pp
    return stems.get(Path(target).stem)


def _query_sqlite(args):
    """Query the index from SQLite."""
    if sqlite_db.resolve_project(args.project) is None:
        print(f"Error: project '{args.project}' is not indexed. Available: {', '.join(sqlite_db.list_project_names())}")
        sys.exit(1)
    results = sqlite_db.find_symbol(args.symbol, project_path=args.project)
    if not results:
        print(f"No symbols matching '{args.symbol}'")
    for sym in results:
        print(f"  {sym['name']} — {sym['file_path']}:{sym['line_start']}-{sym['line_end']}")
        print(f"    {sym.get('signature', '')}")


def _query_mongodb(args):
    """Query the index from MongoDB."""
    resolved = _resolve_project_mongo(args.project)
    if resolved is None:
        print(f"Error: project '{args.project}' is not indexed. Run 'cil status --mongo' to list projects.")
        sys.exit(1)
    col = get_collection()
    doc = col.find_one({"project_path": resolved}, {"file_indices": 1, "_id": 0})
    found = False
    if doc:
        for fi in doc.get("file_indices", {}).values():
            for sym in fi.get("symbols", []):
                if args.symbol.lower() in sym.get("name", "").lower():
                    print(f"  {sym['name']} — {sym['file_path']}:{sym['line_start']}-{sym['line_end']}")
                    print(f"    {sym.get('signature', '')}")
                    found = True
    if not found:
        print(f"No symbols matching '{args.symbol}'")


def _anomalies_sqlite(args):
    """List anomalies from SQLite."""
    if sqlite_db.resolve_project(args.project) is None:
        print(f"Error: project '{args.project}' is not indexed. Available: {', '.join(sqlite_db.list_project_names())}")
        sys.exit(1)
    results = sqlite_db.get_anomalies(severity=args.severity, project_path=args.project)
    if args.file:
        results = [a for a in results if args.file in a.get("file_path", "")]

    if not results:
        print("No anomalies found")
    else:
        severity_colors = {"high": "HIGH", "medium": "MED", "low": "LOW"}
        for a in results:
            sev = severity_colors.get(a.get("severity", ""), "?")
            print(f"  [{sev}] {a['file_path']}:{a['line']} [{a['type']}] {a['message']}")
        print(f"\nTotal: {len(results)} anomalies")


def _anomalies_mongodb(args):
    """List anomalies from MongoDB."""
    resolved = _resolve_project_mongo(args.project)
    if resolved is None:
        print(f"Error: project '{args.project}' is not indexed. Run 'cil status --mongo' to list projects.")
        sys.exit(1)
    col = get_collection()
    doc = col.find_one({"project_path": resolved}, {"anomalies": 1, "_id": 0})
    results = []
    if doc:
        for a in doc.get("anomalies", []):
            if args.severity and a.get("severity") != args.severity:
                continue
            if args.file and args.file not in a.get("file_path", ""):
                continue
            results.append(a)

    if not results:
        print("No anomalies found")
    else:
        severity_colors = {"high": "HIGH", "medium": "MED", "low": "LOW"}
        for a in results:
            sev = severity_colors.get(a.get("severity", ""), "?")
            print(f"  [{sev}] {a['file_path']}:{a['line']} [{a['type']}] {a['message']}")
        print(f"\nTotal: {len(results)} anomalies")


def _enrich_sqlite():
    """Run LLM enrichment on SQLite index."""
    from cil.enricher.enricher import SemanticEnricher

    enricher = SemanticEnricher()
    if not enricher.api_key:
        print("Warning: OPENAI_API_KEY not set, skipping LLM enrichment")
        return

    projects = sqlite_db.get_status()
    for p in projects:
        cil_index = sqlite_db.load_index(p["project_path"])
        if not cil_index:
            continue

        # Build source lines map
        source_map = {}
        for file_path in cil_index.file_indices:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    source_map[file_path] = f.read()
            except Exception:
                pass

        batch_size = 10
        for file_path, fi in cil_index.file_indices.items():
            for i in range(0, len(fi.symbols), batch_size):
                batch = fi.symbols[i:i + batch_size]
                print(f"  Enriching {file_path}:{batch[0].name}+{len(batch)-1}...")
                enriched = enricher.enrich_batch(batch, source_map)
        for j, sym in enumerate(enriched):
                fi.symbols[i + j] = sym

        db_path = sqlite_db.resolve_project(cil_index.project_path)
        if db_path is None:
            print(f"  Skipping {cil_index.project_path}: not indexed")
            continue
        sqlite_db.store_index(cil_index, db_path.parent.name, db_path)


def _enrich_mongodb():
    """Run LLM enrichment on MongoDB index."""
    from cil.enricher.enricher import SemanticEnricher

    col = get_collection()
    for doc in col.find({}, {"file_indices": 1, "_id": 0}):
        file_indices = doc.get("file_indices", {})
        enricher = SemanticEnricher()
        if not enricher.api_key:
            print("Warning: OPENAI_API_KEY not set, skipping LLM enrichment")
            continue

        # Build source lines map
        source_map = {}
        for file_path in file_indices:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    source_map[file_path] = f.read()
            except Exception:
                pass

        batch_size = 10
        for file_path, fi in file_indices.items():
            symbols = fi.get("symbols", [])
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i + batch_size]
                print(f"  Enriching {file_path}:{batch[0]['name']}+{len(batch)-1}...")
                # Convert to SymbolInfo objects
                from cil.models import SymbolInfo
                sym_objects = [SymbolInfo(**s) for s in batch]
                enriched = enricher.enrich_batch(sym_objects, source_map)
                # Write back
                for j, sym in enumerate(enriched):
                    symbols[i + j].update(sym.model_dump(exclude_unset=True))


def _sqlite_command(args):
    """Handle SQLite-specific commands."""
    if args.sqlite_command == "init":
        err = sqlite_db.validate_project_name(args.name)
        if err:
            print(f"Error: {err}")
            sys.exit(1)
        sqlite_db.initialize_db(args.name, sqlite_db.get_project_db_path(args.name))
        print(f"Database initialized at {sqlite_db.get_project_db_path(args.name)}")

    elif args.sqlite_command == "migrate":
        sqlite_db.migrate_from_mongodb()

    elif args.sqlite_command == "query":
        if sqlite_db.resolve_project(args.project) is None:
            print(f"Error: project '{args.project}' is not indexed. Available: {', '.join(sqlite_db.list_project_names())}")
            sys.exit(1)
        results = sqlite_db.find_symbol(args.symbol, project_path=args.project)
        print(json.dumps(results, indent=2, default=str))

    elif args.sqlite_command == "remove":
        sqlite_db.remove_project(args.project_path)
        print(f"Removed project: {args.project_path}")

    elif args.sqlite_command == "prune":
        pruned = sqlite_db.prune_invalid_paths()
        if pruned:
            print(f"Pruned {len(pruned)} invalid path(s):")
            for p in pruned:
                print(f"  - {p}")
        else:
            print("No invalid paths to prune.")

    elif args.sqlite_command == "prune-index":
        deleted = sqlite_db.prune_inactive_rows(args.project_path)
        print(f"Pruned {deleted} inactive row(s) from index tables.")

    else:
        print("Unknown SQLite command. Use: init, migrate, query, remove, prune, prune-index")


if __name__ == "__main__":
    main()

