import re
from cil.indexer.anomaly_detector.base import BaseAnalyzer
from cil.indexer.anomaly_detector.utils import (
    check_long_functions,
    check_deep_nesting,
    check_hardcoded_secrets,
)
from cil.indexer.ast_parser import _get_parser

class CAnalyzer(BaseAnalyzer):
    FUNC_TYPES = ("function_definition",)
    NESTING_TYPES = ("if_statement", "for_statement", "while_statement", "switch_statement")

    def analyze(self, file_path: str, symbols: list, imports: list[str]) -> list[dict]:
        anomalies: list[dict] = []
        parser = _get_parser(".c")
        if not parser:
            return anomalies
        with open(file_path, "rb") as f:
            source = f.read()
        tree = parser.parse(source)
        root_node = tree.root_node
        self._check_resource_leak_malloc(root_node, file_path, anomalies)
        self._check_resource_leak_fopen(root_node, file_path, anomalies)
        self._check_gets_usage(root_node, file_path, anomalies)
        self._check_scanf_no_width(root_node, file_path, anomalies)
        self._check_strcpy_strcat(root_node, file_path, anomalies)
        check_long_functions(root_node, file_path, anomalies, self.FUNC_TYPES)
        check_deep_nesting(root_node, file_path, anomalies, self.NESTING_TYPES)
        check_hardcoded_secrets(root_node, file_path, anomalies, "assignment_expression", self._c_assign_target)
        return anomalies

    def _check_resource_leak_malloc(self, root_node, file_path, anomalies):
        malloc_nodes = set()
        free_calls = set()
        for node in self._walk_all(root_node):
            if node.type == "call_expression":
                func_name = None
                for child in node.children:
                    if child.type == "identifier" and child.text.decode() in ("malloc", "calloc", "realloc"):
                        func_name = child.text.decode()
                        break
                if func_name:
                    malloc_nodes.add(id(node))
        for node in self._walk_all(root_node):
            if node.type == "call_expression":
                for child in node.children:
                    if child.type == "identifier" and child.text.decode() == "free":
                        free_calls.add(id(node))
        if malloc_nodes and not free_calls:
            for node_id in list(malloc_nodes)[:1]:
                pass
        for node in self._walk_all(root_node):
            if node.type == "call_expression":
                func_name = None
                for child in node.children:
                    if child.type == "identifier" and child.text.decode() in ("malloc", "calloc", "realloc"):
                        func_name = child.text.decode()
                        self._add_anomaly(anomalies, "resource_leak_malloc", "high", file_path, self._node_line(node), f"Use of {func_name}() — ensure corresponding free() exists on all code paths")
                        break

    def _check_resource_leak_fopen(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "call_expression":
                continue
            func_name = None
            for child in node.children:
                if child.type == "identifier" and child.text.decode() == "fopen":
                    func_name = "fopen"
                    break
            if func_name:
                self._add_anomaly(anomalies, "resource_leak_fopen", "high", file_path, self._node_line(node), "Use of fopen() — ensure corresponding fclose() exists on all code paths")

    def _check_gets_usage(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "call_expression":
                continue
            for child in node.children:
                if child.type == "identifier" and child.text.decode() == "gets":
                    self._add_anomaly(anomalies, "gets_usage", "critical", file_path, self._node_line(node), "Use of gets() — buffer overflow vulnerability; use fgets() instead")
                    break

    def _check_scanf_no_width(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "call_expression":
                continue
            is_scanf = False
            for child in node.children:
                if child.type == "identifier" and child.text.decode() in ("scanf", "sscanf"):
                    is_scanf = True
                    break
            if not is_scanf:
                continue
            for child in self._walk_all(node):
                if child.type == "string_literal":
                    text = child.text.decode()
                    if "%s" in text and not re.search(r'%\d+s', text):
                        self._add_anomaly(anomalies, "scanf_no_width", "high", file_path, self._node_line(node), "scanf with %s without width limit — potential buffer overflow; specify max width like %50s")

    def _check_strcpy_strcat(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "call_expression":
                continue
            func_name = None
            for child in node.children:
                if child.type == "identifier" and child.text.decode() in ("strcpy", "strcat"):
                    func_name = child.text.decode()
                    break
            if func_name:
                safe_alt = f"{func_name}_s"
                self._add_anomaly(anomalies, "strcpy_strcat", "high", file_path, self._node_line(node), f"Use of {func_name}() — potential buffer overflow; use {safe_alt} or snprintf instead")

    @staticmethod
    def _c_assign_target(node) -> str | None:
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode()
        return None