# Code Intelligence Layer (CIL) — Queryable Codebase Index for AI Agents

> **Why AI agents shouldn't read files.**

## The Problem

Every AI coding agent today uses `read_file` as its primary tool for understanding code. This is the wrong primitive.

`read_file` was designed for humans who skim. Agents don't skim — they ingest everything linearly into a fixed context window. A single 500-line file consumes ~4,000 tokens. Ten files across one session means 40,000 tokens of raw source code riding the KV cache, most of it never referenced again.

The problem isn't the agent. It's the primitive.

## The Solution

CIL replaces `read_file` with a **queryable semantic index**. The agent never reads a file. It queries a knowledge layer that already understands the codebase.

```
read_file("server.py")      →  4,000 tokens of raw code
cil_file_summary("server.py") →  ~200 tokens of structured understanding
```

Same tool call pattern. Same agent mental model. 20× less context consumption.

## How It Works

```
Codebase
    ↓
[tree-sitter]        — language-native AST parsing (Python, TS, JS, Go, Rust, Java, C)
    ↓
[Indexer]            — symbol extraction, call graph, mutation tracking
    ↓
[Anomaly Detector]   — static analysis: thread safety, silent exceptions, resource leaks
    ↓
[SQLite]             — structured, queryable, persistent (per-project DBs)
    ↓
[MCP Tools]          ← agent queries here instead of reading files
    ↓
  Agent
```

**Indexing runs once** (or when you re-index). Every agent read after that is a SQLite lookup — effectively free. The expensive work is amortized across the lifetime of each file version.

## Supported Languages

CIL uses [tree-sitter](https://tree-sitter.github.io/) for language-native parsing:

| Language | Extensions | Symbols Extracted |
|---|---|---|
| Python | `.py`, `.pyi` | functions, classes, decorators, assignments |
| TypeScript | `.ts`, `.tsx` | functions, classes, interfaces, type aliases, variables |
| JavaScript | `.js`, `.jsx` | functions, classes, variables, exports |
| Go | `.go` | functions, methods, structs, consts |
| Rust | `.rs` | functions, structs, consts, macros |
| Java | `.java` | methods, constructors, classes, variables |
| C | `.c`, `.h` | functions, structs, declarations, macros |

## Installation

```bash
# Clone and install (editable mode recommended for development)
git clone https://github.com/your-org/cil.git
cd cil
pip install -e .

# Or install directly
pip install cil
```

### Environment Setup

CIL defaults to MongoDB. To use SQLite instead, pass `--sqlite` to any command, or set `CIL_SQLITE_DB`.

For MongoDB, set `MONGO_URI` in `.env-local`:

```bash
# Copy and edit the environment file
cp .env .env-local
# Edit .env-local with your MONGO_URI
```

The `.env` file is git-ignored. Use `.env-local` for your own credentials.

## Quick Start

```bash
# 1. Index a project (use --sqlite for SQLite storage)
cil index /path/to/your/project --sqlite

# 2. Check what's indexed
cil status --sqlite

# 3. Query for a symbol
cil query "my_function" --sqlite

# 4. List anomalies
cil anomalies --severity high --sqlite
```

Without `--sqlite`, CIL uses MongoDB as the storage backend.

## CLI Reference

### `cil index <project_path>`

Index a project directory. Parses all supported file types, extracts symbols, builds call graph, detects anomalies.

| Flag | Description |
|---|---|
| `--sqlite` | Use SQLite storage (default is MongoDB) |
| `--enrich` | Run LLM semantic enrichment (purpose, complexity, risk scoring) |
| `--incremental` | Only re-index files that have changed since last index |
| `--force` | Clear old index before re-indexing |

```bash
cil index /path/to/project --sqlite
cil index /path/to/project --sqlite --enrich
cil index /path/to/project --sqlite --incremental
cil index /path/to/project --sqlite --force
```

### `cil status`

Show index freshness and stats for all indexed projects.

| Flag | Description |
|---|---|
| `--sqlite` | Use SQLite storage (default is MongoDB) |

```bash
cil status --sqlite
# Output:
#   /path/to/project (v1) — 2026-06-29 11:51:54 (532 files, 1139 symbols)
```

### `cil query <symbol>`

Find a symbol across all indexed projects.

| Flag | Description |
|---|---|
| `--sqlite` | Use SQLite storage (default is MongoDB) |

```bash
cil query "updateStatus" --sqlite
# Output:
#   updateStatus — src/server.py:42-89
#     def updateStatus(status: str)
```

### `cil anomalies`

List detected anomalies across all indexed projects.

| Flag | Description |
|---|---|
| `--sqlite` | Use SQLite storage (default is MongoDB) |
| `--severity low|medium|high` | Filter by severity |
| `--file <path>` | Filter by file path |

```bash
cil anomalies --sqlite
cil anomalies --sqlite --severity high
cil anomalies --sqlite --file src/server.py
```

### `cil watch <project_path>`

Watch a directory for file changes and auto-reindex. Uses `watchdog` with a 2-second debounce. Requires a prior index — if no previous index exists, file changes are silently skipped.

| Flag | Description |
|---|---|
| `--sqlite` | Use SQLite storage (default is MongoDB) |
| `--enrich` | Run LLM semantic enrichment on re-index |

```bash
cil watch /path/to/project --sqlite
cil watch /path/to/project --sqlite --enrich
```

### `cil remove <project_path>`

Remove a project from the SQLite index (deletes project record and per-project DB file).

```bash
cil remove /path/to/project
# Output: Removed project: /path/to/project
```

### `cil enrich`

Run LLM semantic enrichment on existing index. Requires `OPENAI_API_KEY` in environment. Configurable via:
- `OPENAI_BASE_URL` — custom API endpoint (any OpenAI-compatible server)
- `CIL_LLM_MODEL` — model name (default: `gpt-4o-mini`)

| Flag | Description |
|---|---|
| `--sqlite` | Use SQLite storage (default is MongoDB) |

```bash
cil enrich --sqlite
```

### `cil serve`

Start the MCP server over stdio for agent integration.

| Flag | Description |
|---|---|
| `--sqlite` | Use SQLite storage (default is MongoDB) |

```bash
cil serve --sqlite
```

### `cil sqlite` Subcommands

SQLite-specific database management.

#### `cil sqlite init`

Initialize the SQLite database schema. Requires `CIL_SQLITE_DB` to be set.

```bash
export CIL_SQLITE_DB=/path/to/cil.db
cil sqlite init
```

#### `cil sqlite migrate`

Migrate data from MongoDB to SQLite.

```bash
cil sqlite migrate
```

#### `cil sqlite query <symbol>`

Query the SQLite database for a symbol (JSON output).

```bash
cil sqlite query "updateStatus"
```

#### `cil sqlite remove <project_path>`

Remove a project from SQLite (same as `cil remove`).

```bash
cil sqlite remove /path/to/project
```

## Storage

### SQLite (Default)

CIL uses SQLite by default — no external database required. Each project gets its own database file stored at:

```
~/.cil/projects/<project_name>/<project_name>.db
```

For example, indexing `/Users/efe/projects/nibia/api` creates:

```
~/.cil/projects/api/api.db
```

### Single-DB Mode

Set `CIL_SQLITE_DB` to use a single database file for all projects:

```bash
export CIL_SQLITE_DB=/path/to/cil.db
cil index /path/to/project --sqlite
```

### MongoDB (Optional)

MongoDB is supported as an alternative backend. Set `MONGO_URI` in `.env-local`:

```bash
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/cil
```

Then index without `--sqlite`:

```bash
cil index /path/to/project
```

## MCP Server Setup

The MCP server exposes CIL as tools to any MCP-compatible agent (OpenCode, Claude Code, etc.).

### OpenCode Integration

Add the MCP server to your `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "cil": {
      "type": "local",
      "command": [
        "/usr/local/bin/python3",
        "/path/to/cil/cil-mcp-entrypoint.py"
      ],
      "enabled": true,
      "timeout": 120000
    }
  }
}
```

The entrypoint script (`cil-mcp-entrypoint.py`) is pre-configured to use SQLite. It inserts the CIL source path, sets the working directory, and starts the server with `use_sqlite=True`.

### Standalone MCP Server

Run the MCP server directly:

```bash
cil serve --sqlite
```

Or use the entrypoint:

```bash
python3 /path/to/cil/cil-mcp-entrypoint.py
```

### Claude Code Integration

Add to your Claude Code MCP configuration:

```json
{
  "mcpServers": {
    "cil": {
      "command": "python3",
      "args": ["/path/to/cil/cil-mcp-entrypoint.py"]
    }
  }
}
```

## MCP Tools Reference

All tools are available when the MCP server is connected. The server uses SQLite by default.

### `cil_db_status`

Check database connectivity and backend status.

```json
{
  "status": "ok",
  "backend": "sqlite",
  "db_path": "/Users/efe/.cil/projects"
}
```

### `cil_index_project(project_path, enrich?, incremental?)`

Index a project directory.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_path` | string | yes | Absolute path to the project directory |
| `enrich` | boolean | no | Run LLM semantic enrichment (default: false) |
| `incremental` | boolean | no | Only re-index changed files (default: false) |

### `cil_file_summary(path)`

Get file-level summary and symbol list from the index.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path (e.g., `src/cil/indexer/ast_parser.py`) |

### `cil_find_symbol(name)`

Find a symbol across all indexed projects. Returns signature, line range, decorators, and semantic enrichment.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Symbol name (e.g., `updateStatus`, `Delivery`) |

### `cil_trace_mutations(target)`

Trace all writes to a variable or global state. Returns every location that assigns, augments, or deletes the target.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `target` | string | yes | Variable or state to trace (e.g., `_VISION_READY`, `delivery.status`) |

### `cil_trace_calls(func_name)`

Find callers and callees for a function. Returns the full call graph up and down.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `func_name` | string | yes | Function name to trace (e.g., `do_swap`, `updateStatus`) |

### `cil_get_anomalies(severity?)`

Return all pre-computed anomaly flags. Filterable by severity.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `severity` | string | no | Filter by severity (e.g., `low`, `medium`, `high`) |

### `cil_get_body(file, start?, end?)`

Get raw lines from a file. Use only when symbol info is insufficient.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file` | string | yes | File path |
| `start` | integer | no | Start line, 1-indexed (default: 1) |
| `end` | integer | no | End line (default: 100) |

### `cil_status`

Return index freshness and stats for all indexed projects.

No parameters.

## API Server

CIL includes a Flask API server for HTTP access to the index.

```bash
python3 -c "from cil.api.server import create_app; app = create_app(use_sqlite=True); app.run()"
```

### Endpoints

| Method | Route | Description |
|---|---|---|
| GET | `/cil/health` | Health check |
| GET | `/cil/status` | Index freshness and stats |
| POST | `/cil/index` | Trigger re-index (body: `{"project_path": "...", "enrich": true, "incremental": true}`) |
| GET | `/cil/symbol/<name>` | Find symbol |
| GET | `/cil/mutations/<target>` | Trace mutations |
| GET | `/cil/calls/<func_name>` | Trace calls |
| GET | `/cil/body` | Get raw file lines (query: `file`, `start`, `end`) |
| GET | `/cil/file/<path:path>` | File summary |
| GET | `/cil/anomalies` | Anomaly detection |

## Agent Usage Pattern

The core rule: **query the index before reading any file.**

### Workflow

1. **Find what you need** — use `cil_find_symbol` to locate relevant code
2. **Understand the structure** — use `cil_file_summary` to see what a file contains
3. **Trace relationships** — use `cil_trace_calls` or `cil_trace_mutations` to understand data flow
4. **Check for issues** — use `cil_get_anomalies` to find pre-computed problems
5. **Read only what's necessary** — use `cil_get_body` as a last resort for specific lines

### Example: Investigating a Bug

```
# 1. Find the function
cil_find_symbol("processOrder")

# 2. See what calls it
cil_trace_calls("processOrder")

# 3. Check for anomalies in that file
cil_get_anomalies()

# 4. Only now, read the specific lines
cil_get_body("src/orders.py", 142, 160)
```

### Example: Understanding a Variable

```
# 1. Find where the variable is written
cil_trace_mutations("_VISION_READY")

# 2. Find the symbol definition
cil_find_symbol("_VISION_READY")

# 3. Read the definition lines
cil_get_body("src/config.py", 10, 25)
```

## Status

- [x] Architecture designed and validated
- [x] Static indexer — tree-sitter AST parsing, call graph, mutation tracking
- [x] Anomaly detection — thread safety, silent exceptions, resource leaks
- [x] Multi-language support — Python, TypeScript, JavaScript, Go, Rust, Java, C
- [x] SQLite storage — per-project databases, no external dependencies
- [x] MongoDB storage — optional alternative backend
- [x] MCP integration — OpenCode, Claude Code, any MCP-compatible agent
- [x] LLM semantic enrichment — purpose generation, risk scoring (optional, `--enrich`)
- [x] File watch mode — auto-reindex on change (`cil watch`)
- [x] Incremental indexing — only re-index changed files (`--incremental`)
- [x] Flask API server — HTTP access to all index operations
- [x] Project removal — clean up indexed projects (`cil remove`)
- [x] MongoDB migration — migrate existing data to SQLite (`cil sqlite migrate`)
