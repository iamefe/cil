import logging
import os

from abc import ABC, abstractmethod

_MAX_FILE_SIZE = int(os.environ.get("CIL_MAX_FILE_SIZE", 5 * 1024 * 1024))

class BaseAnalyzer(ABC):
    @staticmethod
    def _is_file_oversized(file_path: str) -> bool:
        """Return True if the file exceeds the configured maximum size."""
        try:
            return os.path.getsize(file_path) > _MAX_FILE_SIZE
        except OSError:
            return False
    @abstractmethod
    def analyze(self, file_path: str, symbols: list, imports: list[str]) -> list[dict]:
        pass

    def _add_anomaly(self, anomalies: list[dict], atype: str, severity: str, file_path: str, line: int, message: str):
        anomalies.append({
            "type": atype,
            "severity": severity,
            "file_path": file_path,
            "line": line,
            "message": message,
        })

    @staticmethod
    def _node_line(node) -> int:
        return node.start_point[0] + 1

    @staticmethod
    def _get_node_name(node) -> str | None:
        for child in node.children:
            if child.type in ("identifier", "type_identifier", "property_identifier"):
                return child.text.decode()
        return None

    @staticmethod
    def _find_child(node, child_type: str):
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    @staticmethod
    def _find_all_children(node, child_type: str):
        results = []
        for child in node.children:
            if child.type == child_type:
                results.append(child)
        return results

    @staticmethod
    def _walk_all(node):
        yield node
        for child in node.children:
            yield from BaseAnalyzer._walk_all(child)

    def _walk(self, node, visitor_fn):
        visitor_fn(node)
        for child in node.children:
            self._walk(child, visitor_fn)