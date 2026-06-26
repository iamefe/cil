# CIL — Code Intelligence Layer

## Rules

### CIL Tools

When working with the CIL codebase, use the `cil` CLI for indexing and querying:

```bash
# Index a project
cil index /path/to/project

# Index with LLM enrichment
cil index /path/to/project --enrich

# Re-index (clears old data)
cil index /path/to/project --force

# Query symbols
cil query "function_name"

# List anomalies
cil anomalies --severity high

# Check status
cil status
```

### Testing

```bash
PYTHONPATH=src python3 -m pytest tests/ -v
```

### Key Patterns
- MongoDB stores raw dicts — never reconstruct `CILIndex` from partial queries
- `cil/__init__.py` uses lazy Flask loading via `get_app()` — never import Flask at module level
- Anomaly detection runs during indexing; LLM enrichment is optional (`--enrich`)
- AST parser only captures top-level imports (not inside function bodies)
- Use `ast.Constant` instead of `ast.Str` for Python 3.14 compatibility

### Dependencies
- Flask is an optional dependency — not required for CLI or core indexing
- MongoDB is required for persistence
- OpenAI-compatible API is required for LLM enrichment (`--enrich`)
