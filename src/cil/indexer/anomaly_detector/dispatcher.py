from cil.indexer.anomaly_detector.python_analyzer import PythonAnalyzer
from cil.indexer.anomaly_detector.typescript_analyzer import TypeScriptAnalyzer
from cil.indexer.anomaly_detector.javascript_analyzer import JavaScriptAnalyzer
from cil.indexer.anomaly_detector.go_analyzer import GoAnalyzer
from cil.indexer.anomaly_detector.rust_analyzer import RustAnalyzer
from cil.indexer.anomaly_detector.java_analyzer import JavaAnalyzer
from cil.indexer.anomaly_detector.c_analyzer import CAnalyzer

EXTENSION_MAP = {
    ".py": PythonAnalyzer,
    ".pyi": PythonAnalyzer,
    ".ts": TypeScriptAnalyzer,
    ".tsx": TypeScriptAnalyzer,
    ".js": JavaScriptAnalyzer,
    ".jsx": JavaScriptAnalyzer,
    ".go": GoAnalyzer,
    ".rs": RustAnalyzer,
    ".java": JavaAnalyzer,
    ".c": CAnalyzer,
    ".h": CAnalyzer,
}

class AnomalyDetector:
    def __init__(self):
        self._analyzers: dict[str, object] = {}
        for ext, cls in EXTENSION_MAP.items():
            self._analyzers[ext] = cls()

    def analyze_file(self, file_path: str, symbols: list, imports: list[str]) -> list[dict]:
        from os.path import splitext
        ext = splitext(file_path)[1]
        analyzer = self._analyzers.get(ext)
        if analyzer is None:
            return []
        try:
            return analyzer.analyze(file_path, symbols, imports)
        except Exception:
            return []