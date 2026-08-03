# Code Intelligence Layer (CIL) — Queryable Codebase Index for AI Agents

> **Why AI agents should index before they read.**

## The Problem

Every AI coding agent today uses `read_file` as its primary tool for understanding code. For large files, this is inefficient.

`read_file` was designed for humans who skim. Agents don't skim — they ingest everything linearly into a fixed context window. A single 500-line file consumes ~4,000 tokens. Ten files across one session means 40,000 tokens of raw source code riding the KV cache, most of it never referenced again.

The problem isn't the agent or the tool. It's reading blind.

## The Solution

CIL sits between the agent and `read_file` as a queryable structural index. Instead of reading entire files blindly, the agent queries for symbols first, then fetches only the lines it needs via `cil_get_body`. For large files (>200 lines), this typically saves 60–70% of tokens compared to full-file reads.

```
# Reading blindly vs. reading surgically (example: 500-line server.py)

read_file("server.py")                  →  ~4,000 tokens (everything)
cil_file_summary + targeted cil_get_body →  ~1,200 tokens (map + only what you need)

# For small files (<50 lines), read_file is often cheaper than index lookup overhead.
```

## How It Works

```
Codebase
    ↓
[tree-sitter]        — language-native AST parsing (Python, TS, JS, Go, Rust, Java, C)
    ↓
[Indexer]            — symbol extraction, call graph, mutation tracking
    ↓
[Anomaly Detector]   — multi-language static analysis: bare excepts, unwrap calls, unchecked errors, empty catches, gets/strcpy, and more
    ↓
[SQLite]             — structured, queryable, persistent (per-project DBs)
    ↓
[MCP Tools]          ← agent queries for structure, then fetches targeted lines
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

## Anomaly Detection

CIL runs language-native static analysis during indexing — no LLM required. Each language has checks tailored to its common pitfalls:

### Python (12 checks)

| Check | Severity | Example |
|---|---|---|
| `bare_except` | high | `except:` without exception type |
| `bare_raise` | medium | Reraise without context |
| `mutable_default` | high | `def f(x=[])` shared across calls |
| `long_function` | medium | >80 lines |
| `deep_nesting` | medium | >4 levels of if/for/while/with/try |
| `resource_leak` | high | `open()` outside `with` statement |
| `unused_import` | low | Imported but never referenced |
| `global_mutation` | medium | Use of `global` keyword |
| `missing_init` | low | Class with methods but no `__init__` |
| `star_import` | medium | `from x import *` |
| `eval_exec` | high | `eval()` or `exec()` call |
| `hardcoded_secret` | high | String literal assigned to secret-named variable |

### TypeScript (9 checks)

| Check | Severity | Example |
|---|---|---|
| `empty_catch` | medium | `catch {}` swallows errors silently |
| `any_type` | medium | Using `any` defeats TS type safety |
| `long_function` | medium | >80 lines |
| `deep_nesting` | medium | >4 levels |
| `eval_usage` | high | `eval()` call |
| `console_logging` | low | `console.log/debug/warn/error` in source |
| `unused_import` | low | Imported binding never referenced |
| `wildcard_import` | medium | `import * as x from '...'` |
| `hardcoded_secret` | high | Secret-named variable assigned string literal |

### JavaScript (10 checks)

| Check | Severity | Example |
|---|---|---|
| `empty_catch` | medium | `catch {}` |
| `eval_usage` | high | `eval()`, `Function()` constructor, `setTimeout("...")` |
| `with_statement` | high | Deprecated, breaks strict mode |
| `var_declaration` | low | Use of `var` instead of `const`/`let` |
| `long_function` | medium | >80 lines |
| `deep_nesting` | medium | >4 levels |
| `unused_import` | low | Imported but not used |
| `wildcard_import` | medium | `import * as x from '...'` |
| `console_logging` | low | Console calls left in source |
| `hardcoded_secret` | high | Secret-named variable with string literal |

### Go (5 checks)

| Check | Severity | Example |
|---|---|---|
| `unchecked_error` | high | `_ = doSomething()` or dropped error return |
| `blank_identifier_error` | medium | `_` where error conventionally returned |
| `long_function` | medium | >80 lines |
| `deep_nesting` | medium | >4 levels of if/for/select/switch/range |
| `hardcoded_secret` | high | Secret-named variable with string literal |

### Rust (6 checks)

| Check | Severity | Example |
|---|---|---|
| `unwrap_call` | high | `.unwrap()` panics on Err with no diagnostic info |
| `expect_no_message` | medium | `.expect("")` empty message provides no context |
| `unsafe_block` | medium | `unsafe {}` bypasses borrow checker |
| `panic_macro` | low | `panic!` macro usage |
| `long_function` | medium | >80 lines |
| `hardcoded_secret` | high | Secret-named variable with string literal |

### Java (7 checks)

| Check | Severity | Example |
|---|---|---|
| `empty_catch_block` | medium | `catch (Exception e) {}` swallows errors |
| `broad_exception_handling` | medium | Catching `Exception` or `Throwable` directly |
| `unused_import` | low | Imported class never referenced |
| `long_method` | medium | >80 lines |
| `deep_nesting` | medium | >4 levels |
| `raw_type_usage` | low | Raw generics: `List items` instead of `List<String>` |
| `hardcoded_secret` | high | Secret-named variable with string literal |

### C/C++ (8 checks)

| Check | Severity | Example |
|---|---|---|
| `gets_usage` | critical | `gets()` — buffer overflow guaranteed |
| `resource_leak_malloc` | high | `malloc()` without matching `free()` |
| `resource_leak_fopen` | high | `fopen()` without matching `fclose()` |
| `scanf_no_width` | medium | `scanf("%s", ...)` without width specifier |
| `strcpy_strcat` | medium | Unbounded string operations |
| `long_function` | medium | >80 lines |
| `deep_nesting` | medium | >4 levels |
| `hardcoded_secret` | high | Secret-named variable with string literal |

## Installation

### CLI (pip)

```bash
# Install from GitHub
pip install git+https://github.com/iamefe/cil.git

# Or clone and install in editable mode for development
git clone https://github.com/iamefe/cil.git && cd cil && pip install -e .
```

### Agent Skill (70+ agents)

Install CIL as a ready-to-use skill across OpenCode, Claude Code, Cursor, Codex, Windsurf, and more:

```bash
npx skills add iamefe/cil-skills -g
```

This drops a SKILL.md into your agent's skill directory with usage patterns, tool references, and best practices. For manual MCP setup, see [MCP Server Setup](#mcp-server-setup).

### Environment Setup

CIL uses SQLite by default — no external database required. No configuration needed.

Configuration is done via a `.env` file at the project root (git-ignored). Copy the provided template and fill in your values:

```bash
cp .env.example .env
```

| Variable | Purpose | Default |
|---|---|---|
| `MONGO_URI` | MongoDB connection string (required for `--mongo`) | None (SQLite) |
| `OPENAI_API_KEY` | API key for LLM semantic enrichment (`--enrich`) | None (skip) |
| `OPENAI_BASE_URL` | Custom OpenAI-compatible endpoint for enrichment | None (OpenAI) |
| `CIL_LLM_MODEL` | Model name for enrichment | `gpt-4o-mini` |
| `CIL_SQLITE_DB` | Custom SQLite DB path | Auto-created per project |
| `CIL_MAX_FILE_SIZE` | Max file size to index (bytes) | 5 MB |
| `CIL_ALLOWED_DIRS` | Comma-separated allowlist of directories for MCP server | All accessible paths |
| `CIL_API_KEY` | Bearer token for Flask API auth | Disabled (no auth) |
| `CIL_CORS_ORIGINS` | Comma-separated allowed CORS origins | `localhost,127.0.0.1` |

### MongoDB Setup

To use MongoDB instead of SQLite, set `MONGO_URI` in `.env` and pass `--mongo` to any command:

```bash
cil index /path/to/project --mongo
```

The MongoDB URI format: `mongodb+srv://user:pass@cluster.mongodb.net/db_name?retryWrites=true`

### LLM Enrichment Setup

Optional semantic enrichment adds purpose descriptions, complexity scores, and risk flags to symbols. Requires an OpenAI-compatible API key.

Set `OPENAI_API_KEY` in `.env`, then use `--enrich` during indexing or run `cil enrich` on existing data:

```bash
cil index /path/to/project --enrich
# Or enrich after the fact:
cil enrich /path/to/project
```

For a custom endpoint (e.g., local llama.cpp), set `OPENAI_BASE_URL` and `CIL_LLM_MODEL`.

## Quick Start

```bash
# 1. Index a project
cil index /path/to/your/project

# 2. Check what's indexed
cil status

# 3. Query for a symbol
cil query "my_function"

# 4. List anomalies
cil anomalies --severity high
```

To use MongoDB instead of SQLite, add `--mongo` to any command.

## CLI Reference

### `cil index <project_path>`

Index a project directory. Parses all supported file types, extracts symbols, builds call graph, detects anomalies.

| Flag | Description |
|---|---|
| `--mongo` | Use MongoDB storage (default is SQLite) |
| `--enrich` | Run LLM semantic enrichment (purpose, complexity, risk scoring) |
| `--incremental` | Only re-index files that have changed since last index |
| `--force` | Clear old index before re-indexing |

```bash
cil index /path/to/project
cil index /path/to/project --enrich
cil index /path/to/project --incremental
cil index /path/to/project --force
```

### `cil status`

Show index freshness and stats for all indexed projects.

| Flag | Description |
|---|---|
| `--mongo` | Use MongoDB storage (default is SQLite) |

```bash
cil status
# Output:
#   /path/to/project (v1) — 2026-06-29 11:51:54 (532 files, 1139 symbols)
```

### `cil query <symbol>`

Find a symbol across all indexed projects.

| Flag | Description |
|---|---|
| `--mongo` | Use MongoDB storage (default is SQLite) |

```bash
cil query "updateStatus"
# Output:
#   updateStatus — src/server.py:42-89
#     def updateStatus(status: str)
```

### `cil anomalies`

List detected anomalies across all indexed files in all supported languages.

| Flag | Description |
|---|---|
| `--mongo` | Use MongoDB storage (default is SQLite) |
| `--severity low|medium|high` | Filter by severity |
| `--file <path>` | Filter by file path |

```bash
cil anomalies
cil anomalies --severity high
cil anomalies --file src/server.py
```

### `cil watch <project_path>`

Watch a directory for file changes and auto-reindex. Uses `watchdog` with a 2-second debounce. Requires a prior index — if no previous index exists, file changes are silently skipped.

| Flag | Description |
|---|---|
| `--mongo` | Use MongoDB storage (default is SQLite) |
| `--enrich` | Run LLM semantic enrichment on re-index |

```bash
cil watch /path/to/project
cil watch /path/to/project --enrich
```

### `cil watch-all`

Watch all registered paths from the watch database. Validates paths on startup, skips invalid ones, and starts a watcher thread for each valid path. Use with launchd to auto-start at boot.

| Flag | Description |
|---|---|
| `--mongo` | Use MongoDB storage (default is SQLite) |
| `--enrich` | Run LLM semantic enrichment on re-index |

```bash
cil watch-all
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
| `--mongo` | Use MongoDB storage (default is SQLite) |

```bash
cil enrich
```

### `cil serve`

Start the MCP server over stdio for agent integration.

| Flag | Description |
|---|---|
| `--mongo` | Use MongoDB storage (default is SQLite) |

```bash
cil serve
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

#### `cil sqlite prune`

Remove invalid paths from the watch database.

```bash
cil sqlite prune
# Pruned 2 invalid path(s):
#   - /old/path
#   - /another/old/path
```

### `cil prune-index <project_path>`

Permanently delete soft-deleted rows (`status = 'invalid'`) from a project's index. After reindexing, old rows are marked invalid instead of deleted. Use this to reclaim space after confirming the new index is correct.

```bash
cil prune-index /path/to/project
# Pruned 150 invalid rows from /path/to/project
```

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
cil serve
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
- [x] Anomaly detection — multi-language static analysis for Python (12), TypeScript (9), JavaScript (10), Go (5), Rust (6), Java (7), C/C++ (8)
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
- [x] Watch database — track registered paths, validate, prune invalid (`cil sqlite prune`)
- [x] Watch-all mode — watch all registered paths (`cil watch-all`)
- [x] Launchd integration — auto-start watchers at boot (see `com.cil.watch-all.plist`)
- [x] Soft-delete pattern — `status` column in all index tables, `clear_project_data` marks rows invalid instead of deleting
- [x] Schema v3 — `status` column in all index tables for soft-delete support
- [x] Prune-index — permanently delete soft-deleted rows after reindexing

## Launchd Integration

Auto-start CIL watchers at macOS boot using the provided launchd plist.

### Setup

1. Copy the plist to your LaunchAgents directory:

```bash
cp com.cil.watch-all.plist ~/Library/LaunchAgents/
```

2. Load the agent:

```bash
launchctl load ~/Library/LaunchAgents/com.cil.watch-all.plist
```

3. Verify it's running:

```bash
launchctl list | grep cil
# Output: -    0    com.cil.watch-all
```

### Unload

```bash
launchctl unload ~/Library/LaunchAgents/com.cil.watch-all.plist
```

### Logs

```bash
tail -f /tmp/cil-watch-all.log
```

### How It Works

1. Register paths with `cil watch <path>`
2. Paths are stored in `~/.cil/watch.db`
3. `cil watch-all` reads all valid paths and starts a watcher for each
4. launchd runs `cil watch-all` at boot and keeps it alive

### CIL MCP Server

Auto-start the CIL MCP server at boot for agent integration.

#### Setup

1. Copy the plist to your LaunchAgents directory:

```bash
cp com.cil.mcp.plist ~/Library/LaunchAgents/
```

2. Load the agent:

```bash
launchctl load ~/Library/LaunchAgents/com.cil.mcp.plist
```

3. Verify it's running:

```bash
launchctl list | grep cil
# Output: -    0    com.cil.mcp
```

#### Unload

```bash
launchctl unload ~/Library/LaunchAgents/com.cil.mcp.plist
```

#### Logs

```bash
tail -f /tmp/cil-mcp.log
```

#### How It Works

1. The MCP server runs `cil-mcp-entrypoint.py` with SQLite enabled
2. It exposes 9 tools to any MCP-compatible agent
3. launchd keeps the server alive and restarts it on crash

## Soft-Delete Pattern

CIL uses a soft-delete pattern to protect against accidental data loss during reindexing. Instead of permanently deleting rows, old rows are marked with `status = 'invalid'`.

### How It Works

1. **Reindexing** — when a project is reindexed, `clear_project_data()` sets `status = 'invalid'` on all existing rows instead of deleting them
2. **Active queries** — all query methods filter by `status = 'active'`, so invalid rows are invisible to normal operations
3. **Upsert recovery** — if `upsert_file` is called on a previously invalidated file, the `status` is reset to `'active'`, restoring the row
4. **Pruning** — after confirming the new index is correct, use `cil prune-index <project_path>` to permanently delete invalid rows and reclaim space

### Schema v3

Schema version 3 adds a `status` column to all index tables:

| Table | Status Column | Default |
|---|---|---|
| `files` | `status` | `'active'` |
| `symbols` | `status` | `'active'` |
| `imports` | `status` | `'active'` |
| `call_graph` | `status` | `'active'` |
| `mutations` | `status` | `'active'` |
| `anomalies` | `status` | `'active'` |

The migration is applied automatically on first access after the schema version check.

## Troubleshooting

### Python 3.14 Incompatibility

tree-sitter packages produce abi3 wheels that are incompatible with free-threaded Python 3.14 (CPython 3.14t). You'll see `symbol not found` errors on import.

**Fix:** Use Python 3.12 or 3.13 instead:

```bash
pyenv install 3.13.0
pyenv local 3.13.0
pip install cil
```

### Linux: Missing C Toolchain for Language Packs

Some tree-sitter language pack wheels require source compilation on Linux. If you see a build error, install a C compiler and development headers:

- **Debian/Ubuntu:** `sudo apt-get install gcc libc-dev`
- **RHEL/Fedora:** `sudo dnf install gcc glibc-devel`
- **Alpine:** `apk add gcc musl-dev`

Then retry installation: `pip install cil`

### Windows: pip Version

On Windows, use Python 3.12+ with pip >= 23 to ensure tree-sitter wheel resolution works correctly:

```powershell
python -m pip install --upgrade pip setuptools wheel
pip install cil
```
