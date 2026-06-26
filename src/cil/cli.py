import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cil.indexer import Indexer
from cil.database import get_collection, ensure_indexes


def main():
    parser = argparse.ArgumentParser(description="Code Intelligence Layer (CIL)")
    subparsers = parser.add_subparsers(dest="command")

    # Index command
    index_parser = subparsers.add_parser("index", help="Index a Python project")
    index_parser.add_argument("project_path", help="Path to the project directory")
    index_parser.add_argument("--enrich", action="store_true", help="Run LLM semantic enrichment")
    index_parser.add_argument("--force", action="store_true", help="Clear old index before re-indexing")

    # Status command
    subparsers.add_parser("status", help="Show index status")

    # Query command
    query_parser = subparsers.add_parser("query", help="Query the index")
    query_parser.add_argument("symbol", help="Symbol name to find")

    # MCP server command
    subparsers.add_parser("serve", help="Start MCP server")

    # Anomalies command
    anom_parser = subparsers.add_parser("anomalies", help="List detected anomalies")
    anom_parser.add_argument("--severity", choices=["low", "medium", "high"], help="Filter by severity")
    anom_parser.add_argument("--file", help="Filter by file path")

    # Enrich command
    subparsers.add_parser("enrich", help="Run LLM semantic enrichment on existing index")

    args = parser.parse_args()

    # Ensure MongoDB indexes exist
    try:
        ensure_indexes()
    except Exception:
        pass  # Non-critical if DB is down

    if args.command == "index":
        col = get_collection()

        # --force: clear old index before re-indexing
        if args.force:
            col.delete_one({"project_path": args.project_path})

        indexer = Indexer()
        cil_index = indexer.index_directory(args.project_path, enrich=args.enrich)

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

    elif args.command == "status":
        col = get_collection()
        docs = list(col.find({}, {"project_path": 1, "indexed_at": 1, "version": 1, "_id": 0}))
        if not docs:
            print("No indexed projects")
        for doc in docs:
            print(f"  {doc['project_path']} (v{doc.get('version', '?')}) — {doc.get('indexed_at', '?')}")

    elif args.command == "query":
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

    elif args.command == "serve":
        from cil.mcp.server import create_mcp_server
        server = create_mcp_server()
        server()

    elif args.command == "anomalies":
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

    elif args.command == "enrich":
        col = get_collection()
        for doc in col.find({}, {"file_indices": 1, "_id": 0}):
            file_indices = doc.get("file_indices", {})
            from cil.enricher.enricher import SemanticEnricher
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


if __name__ == "__main__":
    main()

