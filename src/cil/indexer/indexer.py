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


class Indexer:
    """Index a Python project directory and produce a CILIndex."""

    def __init__(self):
        self.parser = ASTParser()
        self.detector = AnomalyDetector()

    def index_directory(self, project_path: str, enrich: bool = False) -> CILIndex:
        """Walk the directory, parse all Python files, and build the index."""
        project_path = os.path.abspath(project_path)
        file_indices = {}
        all_calls = []
        all_mutations = []
        all_anomalies = []

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
                try:
                    file_index = self.parser.parse_file(file_path)
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

                except Exception as e:
                    print(f"Warning: failed to parse {file_path}: {e}")

        # Optional LLM enrichment
        if enrich:
            self._llm_enrich(file_indices)

        return CILIndex(
            project_path=project_path,
            file_indices=file_indices,
            call_graph=all_calls,
            mutations=all_mutations,
            anomalies=all_anomalies,
        )

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

