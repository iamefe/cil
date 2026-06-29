import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cil.indexer import Indexer
from cil.database import get_collection, ensure_indexes
from cil import sqlite_db


def main():
    parser = argparse.ArgumentParser(description="Code Intelligence Layer (CIL)")
    subparsers = parser.add_subparsers(dest="command")

    # Index command
    index_parser = subparsers.add_parser("index", help="Index a Python project")
    index_parser.add_argument("project_path", help="Path to the project directory")
    index_parser.add_argument("--enrich", action="store_true", help="Run LLM semantic enrichment")
    index_parser.add_argument("--force", action="store_true", help="Clear old index before re-indexing")
    index_parser.add_argument("--incremental", action="store_true", help="Only re-index changed files")
    index_parser.add_argument("--sqlite", action="store_true", help="Use SQLite instead of MongoDB")

    # Status command
    status_parser = subparsers.add_parser("status", help="Show index status")
    status_parser.add_argument("--sqlite", action="store_true", help="Use SQLite instead of MongoDB")

    # Query command
    query_parser = subparsers.add_parser("query", help="Query the index")
    query_parser.add_argument("symbol", help="Symbol name to find")
    query_parser.add_argument("--sqlite", action="store_true", help="Use SQLite instead of MongoDB")

    # MCP server command
    serve_parser = subparsers.add_parser("serve", help="Start MCP server")
    serve_parser.add_argument("--sqlite", action="store_true", help="Use SQLite instead of MongoDB")

    # Anomalies command
    anom_parser = subparsers.add_parser("anomalies", help="List detected anomalies")
    anom_parser.add_argument("--severity", choices=["low", "medium", "high"], help="Filter by severity")
    anom_parser.add_argument("--file", help="Filter by file path")
    anom_parser.add_argument("--sqlite", action="store_true", help="Use SQLite instead of MongoDB")

    # Watch command
    watch_parser = subparsers.add_parser("watch", help="Watch directory for changes and auto-reindex")
    watch_parser.add_argument("project_path", help="Path to the project directory")
    watch_parser.add_argument("--enrich", action="store_true", help="Run LLM semantic enrichment on re-index")
    watch_parser.add_argument("--sqlite", action="store_true", help="Use SQLite instead of MongoDB")

    # Enrich command
    enrich_parser = subparsers.add_parser("enrich", help="Run LLM semantic enrichment on existing index")
    enrich_parser.add_argument("--sqlite", action="store_true", help="Use SQLite instead of MongoDB")

    # SQLite-specific commands
    sqlite_parser = subparsers.add_parser("sqlite", help="SQLite database management")
    sqlite_sub = sqlite_parser.add_subparsers(dest="sqlite_command")
    sqlite_sub.add_parser("init", help="Initialize SQLite database")
    sqlite_sub.add_parser("migrate", help="Migrate data from MongoDB to SQLite")
    sqlite_query = sqlite_sub.add_parser("query", help="Query SQLite database")
    sqlite_query.add_argument("symbol", help="Symbol name to find")

    args = parser.parse_args()

    # Ensure MongoDB indexes exist (skip if using SQLite-only commands)
    if args.command != "sqlite":
        try:
            ensure_indexes()
        except Exception:
            pass  # Non-critical if DB is down

    if args.command == "index":
        if args.sqlite:
            _index_sqlite(args)
        else:
            _index_mongodb(args)

    elif args.command == "status":
        if args.sqlite:
            _status_sqlite()
        else:
            _status_mongodb()

    elif args.command == "query":
        if args.sqlite:
            _query_sqlite(args)
        else:
            _query_mongodb(args)

    elif args.command == "serve":
        from cil.mcp.server import create_mcp_server
        server = create_mcp_server(use_sqlite=args.sqlite)
        server()

    elif args.command == "anomalies":
        if args.sqlite:
            _anomalies_sqlite(args)
        else:
            _anomalies_mongodb(args)

    elif args.command == "watch":
        from cil.watcher import FileWatcher
        watcher = FileWatcher(args.project_path, enrich=args.enrich, use_sqlite=args.sqlite)
        watcher.start()

    elif args.command == "enrich":
        if args.sqlite:
            _enrich_sqlite()
        else:
            _enrich_mongodb()

    elif args.command == "sqlite":
        _sqlite_command(args)


def _index_sqlite(args):
    """Index a project using SQLite storage."""
    from cil.models import CILIndex

    project_path = os.path.abspath(args.project_path)

    # --force: clear old index
    if args.force:
        sqlite_db.delete_project(project_path)

    # Load previous index for incremental mode
    previous_index = None
    if args.incremental:
        previous_index = sqlite_db.load_index(project_path)
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

    sqlite_db.store_index(cil_index)

    print(f"Indexed {cil_index.project_path}")
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
    sqlite_db.initialize_db()
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


def _query_sqlite(args):
    """Query the index from SQLite."""
    sqlite_db.initialize_db()
    results = sqlite_db.find_symbol(args.symbol)
    if not results:
        print(f"No symbols matching '{args.symbol}'")
    for sym in results:
        print(f"  {sym['name']} — {sym['file_path']}:{sym['line_start']}-{sym['line_end']}")
        print(f"    {sym.get('signature', '')}")


def _query_mongodb(args):
    """Query the index from MongoDB."""
    col = get_collection()
    found = False
    for doc in col.find({}, {"file_indices": 1, "_id": 0}):
        file_indices = doc.get("file_indices", {})
        for fi in file_indices.values():
            for sym in fi.get("symbols", []):
                if args.symbol.lower() in sym.get("name", "").lower():
                    print(f"  {sym['name']} — {sym['file_path']}:{sym['line_start']}-{sym['line_end']}")
                    print(f"    {sym.get('signature', '')}")
                    found = True
    if not found:
        print(f"No symbols matching '{args.symbol}'")


def _anomalies_sqlite(args):
    """List anomalies from SQLite."""
    sqlite_db.initialize_db()
    results = sqlite_db.get_anomalies(severity=args.severity)
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
    col = get_collection()
    results = []
    for doc in col.find({}, {"anomalies": 1, "_id": 0}):
        anomalies = doc.get("anomalies", [])
        for a in anomalies:
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

    sqlite_db.initialize_db()
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

        sqlite_db.store_index(cil_index)


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
        sqlite_db.initialize_db()
        print(f"Database initialized at {sqlite_db.get_db_path()}")

    elif args.sqlite_command == "migrate":
        sqlite_db.initialize_db()
        sqlite_db.migrate_from_mongodb()

    elif args.sqlite_command == "query":
        sqlite_db.initialize_db()
        results = sqlite_db.find_symbol(args.symbol)
        print(json.dumps(results, indent=2, default=str))

    else:
        print("Unknown SQLite command. Use: init, migrate, query")


if __name__ == "__main__":
    main()

