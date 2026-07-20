from cil.indexer.anomaly_detector.base import BaseAnalyzer
from cil.indexer.anomaly_detector.utils import (
    check_long_functions,
    check_deep_nesting,
    check_hardcoded_secrets,
)
from cil.indexer.ast_parser import _get_parser

class JavaAnalyzer(BaseAnalyzer):
    FUNC_TYPES = ("method_declaration", "constructor_declaration")
    NESTING_TYPES = ("if_statement", "for_statement", "while_statement", "enhanced_for_statement", "switch_statement")

    def analyze(self, file_path: str, symbols: list, imports: list[str]) -> list[dict]:
        anomalies: list[dict] = []
        parser = _get_parser(".java")
        if not parser:
            return anomalies
        with open(file_path, "rb") as f:
            source = f.read()
        tree = parser.parse(source)
        root_node = tree.root_node
        self._check_empty_catch_block(root_node, file_path, anomalies)
        self._check_broad_exception_handling(root_node, file_path, anomalies)
        self._check_unused_imports(root_node, file_path, imports, anomalies)
        check_long_functions(root_node, file_path, anomalies, self.FUNC_TYPES)
        check_deep_nesting(root_node, file_path, anomalies, self.NESTING_TYPES)
        self._check_raw_type_usage(root_node, file_path, anomalies)
        check_hardcoded_secrets(root_node, file_path, anomalies, "local_variable_declaration", self._java_assign_target)
        return anomalies

    def _check_empty_catch_block(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "catch_clause":
                continue
            body = None
            for child in node.children:
                if child.type == "block":
                    body = child
                    break
            if not body:
                continue
            has_content = False
            for child in body.children:
                if child.type not in ("{", "}"):
                    has_content = True
                    break
            if not has_content:
                self._add_anomaly(anomalies, "empty_catch_block", "medium", file_path, self._node_line(node), "Empty catch block — errors are silently swallowed")

    def _check_broad_exception_handling(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "catch_clause":
                continue
            exc_type = None
            for child in self._walk_all(node):
                if child.type == "catch_type":
                    for c in child.children:
                        if c.type == "scoped_identifier":
                            exc_type = c.text.decode().split(".")[-1]
                        elif c.type == "type_identifier":
                            exc_type = c.text.decode()
                    break
            if exc_type and exc_type.endswith("Exception"):
                if exc_type in ("Exception", "RuntimeException"):
                    self._add_anomaly(anomalies, "broad_exception_handling", "low", file_path, self._node_line(node), f"Broad exception handling ({exc_type}) — consider catching specific exceptions")

    def _check_unused_imports(self, root_node, file_path, imports, anomalies):
        used_names: set[str] = set()
        for node in self._walk_all(root_node):
            if node.type == "identifier":
                used_names.add(node.text.decode())
            elif node.type == "type_identifier":
                used_names.add(node.text.decode())
        for imp in imports:
            local_name = imp.split(".")[-1].strip()
            if local_name.startswith("*"):
                continue
            if local_name and local_name not in used_names:
                self._add_anomaly(anomalies, "unused_import", "low", file_path, 0, f"Import '{imp}' appears unused")

    RAW_TYPES = {"List", "ArrayList", "LinkedList", "Map", "HashMap", "TreeMap", "Hashtable", "Set", "HashSet", "LinkedHashSet", "TreeSet", "Queue", "Deque", "Stack", "Vector", "Collection", "Optional"}

    def _check_raw_type_usage(self, root_node, file_path, anomalies):
        seen_lines = set()
        for node in self._walk_all(root_node):
            parent = node.parent if hasattr(node, 'parent') else None
            if parent and parent.type == "generic_type":
                continue
            short_name = None
            if node.type == "type_identifier" and node.text.decode() in self.RAW_TYPES:
                short_name = node.text.decode()
            elif node.type == "scoped_type_identifier":
                parts = [c.text.decode() for c in node.children if c.type == "type_identifier"]
                if parts and parts[-1] in self.RAW_TYPES:
                    short_name = parts[-1]
            if not short_name or node.start_point[0] in seen_lines:
                continue
            seen_lines.add(node.start_point[0])
            self._add_anomaly(anomalies, "raw_type_usage", "medium", file_path, self._node_line(node), f"Raw type usage ({short_name}) — add generic type parameters")

    @staticmethod
    def _java_assign_target(node) -> str | None:
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode()
        return None