# Code Intelligence Layer (CIL)

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
[MongoDB]            — structured, queryable, persistent
    ↓
[MCP Tools]          ← agent queries here instead of reading files
    ↓
  Agent
```

**Indexing runs once** (or when you re-index). Every agent read after that is a MongoDB lookup — effectively free. The expensive work is amortized across the lifetime of each file version.

## Multi-Language

CIL uses [tree-sitter](https://tree-sitter.github.io/) for language-native parsing. Supported languages:

| Language | Extensions | Symbols Extracted |
|---|---|---|
| Python | `.py`, `.pyi` | functions, classes, decorators, assignments |
| TypeScript | `.ts`, `.tsx` | functions, classes, interfaces, type aliases, variables |
| JavaScript | `.js`, `.jsx` | functions, classes, variables, exports |
| Go | `.go` | functions, methods, structs, consts |
| Rust | `.rs` | functions, structs, consts, macros |
| Java | `.java` | methods, constructors, classes, variables |
| C | `.c`, `.h` | functions, structs, declarations, macros |

## What the Agent Gets

Instead of raw code, the agent receives structured understanding:

```json
{
  "name": "Customer",
  "kind": "interface",
  "file_path": "admin_panel/src/types/customer.ts",
  "line_start": 26,
  "line_end": 61,
  "signature": "interface Customer",
  "risk_flags": ["thread unsafe"]
}
```

For auditing, the agent gets **pre-computed anomaly flags** — not raw files to investigate. 90% of audit questions are answerable from the index without a single line of source code.

## Agent Tools (via MCP)

| Tool | Description |
|---|---|
| `cil_index_project(path)` | Index a project directory into MongoDB |
| `cil_file_summary(path)` | File-level summary + all symbols |
| `cil_find_symbol(name)` | Find a symbol across all indexed projects |
| `cil_trace_mutations(var)` | Every place that writes to a variable |
| `cil_trace_calls(fn)` | Full call graph up and down |
| `cil_get_anomalies(severity)` | Pre-computed flags — thread safety, silent exceptions, resource leaks |
| `cil_get_body(file, start, end)` | Raw source lines — last resort only |
| `cil_status()` | Index freshness and stats for all indexed projects |

## Storage

```
MongoDB  →  full index: symbols, call graph, mutations, anomalies, imports
```

Each project is a single document keyed by `project_path`. The filesystem is only read during indexing — raw code is never copied into the database.

## Why This Generalises

CIL is not tied to any single project. It works for any agent that supports MCP tools. The primitive it replaces — `read_file` — exists in every coding agent today: Claude Code, OpenCode, Aider, Codex. Any of them can route through CIL instead.

> *The agent doesn't change. The file system interface does.*

## Status

- [x] Architecture designed and validated
- [x] Static indexer — tree-sitter AST parsing, call graph, mutation tracking
- [x] Anomaly detection — thread safety, silent exceptions, resource leaks
- [x] Multi-language support — Python, TypeScript, JavaScript, Go, Rust, Java, C
- [x] MongoDB storage — persistent, queryable index
- [x] MCP integration — OpenCode tool registration
- [ ] LLM semantic enrichment — purpose generation, risk scoring (optional, `--enrich`)
- [ ] File watch mode — auto-reindex on change
- [ ] Incremental indexing — only re-index changed files
