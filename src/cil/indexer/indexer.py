import hashlib
import os

from cil.indexer.ast_parser import ASTParser
from cil.indexer.anomaly_detector import AnomalyDetector
from cil.models import CILIndex, FileIndex

PYTHON_EXTENSIONS = {".py", ".pyi"}
SUPPORTED_EXTENSIONS = {
    ".py", ".pyi",   # Python
    ".ts", ".tsx",   # TypeScript
    ".js", ".jsx",   # JavaScript
    ".go",           # Go
    ".rs",           # Rust
    ".java",         # Java
    ".c", ".h",      # C
}


def _file_hash(file_path: str) -> str:
    """Compute SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()


class Indexer:
    """Index a Python project directory and produce a CILIndex."""

    def __init__(self):
        self.parser = ASTParser()
        self.detector = AnomalyDetector()

    def index_directory(
        self,
        project_path: str,
        enrich: bool = False,
        incremental: bool = False,
        previous_index: CILIndex | None = None,
    ) -> CILIndex:
        """Walk the directory, parse all Python files, and build the index.

        If incremental=True and previous_index is provided, only re-index
        files whose hash has changed.
        """
        project_path = os.path.abspath(project_path)

        # Build previous hash map for incremental mode
        prev_hashes: dict[str, str] = {}
        prev_file_indices: dict[str, FileIndex] = {}
        prev_calls = []
        prev_mutations = []
        prev_anomalies = []
        if incremental and previous_index:
            for fp, fi in previous_index.file_indices.items():
                prev_hashes[fp] = fi.file_hash
                prev_file_indices[fp] = fi
            prev_calls = list(previous_index.call_graph)
            prev_mutations = list(previous_index.mutations)
            prev_anomalies = list(previous_index.anomalies)

        file_indices = {}
        all_calls = []
        all_mutations = []
        all_anomalies = []
        files_skipped = 0
        files_indexed = 0
        unchanged_files: set[str] = set()

        for root, dirs, files in os.walk(project_path):
            # Skip common non-source directories
            dirs[:] = [d for d in dirs if d not in {
                "__pycache__", ".git", ".venv", "venv", "node_modules",
                ".mypy_cache", ".pytest_cache", "dist", "build", "egg-info",
            }]

            for fname in files:
                ext = os.path.splitext(fname)[1]
                if ext not in SUPPORTED_EXTENSIONS:
                    continue

                file_path = os.path.join(root, fname)
                current_hash = _file_hash(file_path)

                # Incremental: skip unchanged files
                if incremental and prev_hashes.get(file_path) == current_hash:
                    file_indices[file_path] = prev_file_indices[file_path]
                    unchanged_files.add(file_path)
                    files_skipped += 1
                    continue

                try:
                    file_index = self.parser.parse_file(file_path)
                    file_index.file_hash = current_hash
                    file_indices[file_path] = file_index
                    all_calls.extend(self.parser.calls)
                    all_mutations.extend(self.parser.mutations)

                    # Run anomaly detection
                    anomalies = self.detector.analyze_file(
                        file_path, file_index.symbols, file_index.imports
                    )
                    all_anomalies.extend(anomalies)

                    # Enrich symbols with risk_flags from anomalies
                    self._enrich_symbols(file_index, anomalies)

                    files_indexed += 1

                except Exception as e:
                    print(f"Warning: failed to parse {file_path}: {e}")

        # In incremental mode, preserve call graph/mutations/anomalies
        # for unchanged files
        if incremental and unchanged_files:
            for c in prev_calls:
                if c.caller.split(":")[0] in unchanged_files:
                    all_calls.append(c)
            for m in prev_mutations:
                if m.source.split(":")[0] in unchanged_files:
                    all_mutations.append(m)
            for a in prev_anomalies:
                if a.file_path in unchanged_files:
                    all_anomalies.append(a)

        # Optional LLM enrichment
        if enrich:
            self._llm_enrich(file_indices)

        cil_index = CILIndex(
            project_path=project_path,
            file_indices=file_indices,
            call_graph=all_calls,
            mutations=all_mutations,
            anomalies=all_anomalies,
        )

        if incremental:
            print(f"  Incremental: {files_indexed} files indexed, {files_skipped} skipped")

        return cil_index

    def _enrich_symbols(self, file_index: FileIndex, anomalies: list[dict]):
        """Add risk_flags to symbols based on detected anomalies."""
        # Build a map of line -> anomaly types
        line_anomalies = {}
        for a in anomalies:
            line = a["line"]
            if line not in line_anomalies:
                line_anomalies[line] = []
            line_anomalies[line].append(a["type"])

        for sym in file_index.symbols:
            flags = set()
            # Check anomalies at the symbol's start line
            if sym.line_start in line_anomalies:
                flags.update(line_anomalies[sym.line_start])
            # Check anomalies within the symbol's range
            for line in range(sym.line_start, sym.line_end + 1):
                if line in line_anomalies:
                    flags.update(line_anomalies[line])
            sym.risk_flags = sorted(flags)

    def _llm_enrich(self, file_indices: dict[str, FileIndex]):
        """Run LLM enrichment on all symbols."""
        from cil.enricher.enricher import SemanticEnricher

        enricher = SemanticEnricher()
        if not enricher.api_key:
            print("Warning: OPENAI_API_KEY not set, skipping LLM enrichment")
            return

        # Build source lines map
        source_map = {}
        for file_path in file_indices:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    source_map[file_path] = f.read()
            except Exception:
                pass

        # Enrich in batches of 10
        batch_size = 10
        for file_path, fi in file_indices.items():
            for i in range(0, len(fi.symbols), batch_size):
                batch = fi.symbols[i:i + batch_size]
                print(f"  Enriching {file_path}:{batch[0].name}+{len(batch)-1}...")
                enriched = enricher.enrich_batch(batch, source_map)
                # Update in place
                for j, sym in enumerate(batch):
                    fi.symbols[i + j] = enriched[j]

