import re
from cil.indexer.anomaly_detector.base import BaseAnalyzer
from cil.indexer.anomaly_detector.utils import (
    check_long_functions,
    check_deep_nesting,
    check_hardcoded_secrets,
)
from cil.indexer.ast_parser import _get_parser

class PythonAnalyzer(BaseAnalyzer):
    FUNC_TYPES = ("function_definition", "async_function_definition")
    NESTING_TYPES = ("if_statement", "for_statement", "while_statement", "with_statement", "try_statement")

    def analyze(self, file_path: str, symbols: list, imports: list[str]) -> list[dict]:
        anomalies: list[dict] = []
        parser = _get_parser(".py")
        if not parser:
            return anomalies
        with open(file_path, "rb") as f:
            source = f.read()
        tree = parser.parse(source)
        root_node = tree.root_node
        self._check_bare_except(root_node, file_path, anomalies)
        self._check_bare_raise(root_node, file_path, anomalies)
        self._check_mutable_defaults(root_node, file_path, anomalies)
        check_long_functions(root_node, file_path, anomalies, self.FUNC_TYPES)
        check_deep_nesting(root_node, file_path, anomalies, self.NESTING_TYPES)
        self._check_resource_leaks(root_node, file_path, anomalies)
        self._check_unused_imports(root_node, file_path, imports, anomalies)
        self._check_global_mutations(root_node, file_path, anomalies)
        self._check_missing_init(root_node, file_path, anomalies)
        self._check_star_imports(root_node, file_path, anomalies)
        self._check_eval_exec(root_node, file_path, anomalies)
        check_hardcoded_secrets(root_node, file_path, anomalies, "assignment", self._python_assign_target, self._python_value_is_string)
        return anomalies

    def _check_bare_except(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "except_clause":
                continue
            has_type = False
            found_colon = False
            for child in node.children:
                if child.type == ":":
                    found_colon = True
                    break
                if child.type not in ("except",):
                    has_type = True
                    break
            if not has_type:
                self._add_anomaly(anomalies, "bare_except", "high", file_path, self._node_line(node), "Bare except clause catches all exceptions including KeyboardInterrupt and SystemExit")

    def _check_bare_raise(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type == "raise_statement":
                has_exc = False
                for child in node.children:
                    if child.type != "raise":
                        has_exc = True
                        break
                if not has_exc:
                    self._add_anomaly(anomalies, "bare_raise", "medium", file_path, self._node_line(node), "Bare raise without exception — may lose exception context")

    def _check_mutable_defaults(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type not in self.FUNC_TYPES:
                continue
            params = self._find_child(node, "parameters")
            if not params:
                continue
            for child in self._walk_all(params):
                if child.type in ("list", "dictionary", "set"):
                    name = self._get_node_name(node)
                    self._add_anomaly(anomalies, "mutable_default", "high", file_path, self._node_line(node), f"Mutable default argument in {name}() — shared across all calls")

    def _check_resource_leaks(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type == "call":
                func_name = None
                for child in node.children:
                    if child.type == "identifier":
                        func_name = child.text.decode()
                    elif child.type == "attribute":
                        for c in child.children:
                            if c.type == "identifier" and c.prev_sibling and c.prev_sibling.type == ".":
                                func_name = c.text.decode()
                if func_name != "open":
                    continue
                if not self._is_inside_with(node, root_node):
                    self._add_anomaly(anomalies, "resource_leak", "high", file_path, self._node_line(node), "open() without context manager — file may not be closed on error")

    def _is_inside_with(self, target_node, root_node):
        for node in self._walk_all(root_node):
            if node.type == "with_statement":
                for child in self._walk_all(node):
                    if child is target_node:
                        return True
        return False

    def _check_unused_imports(self, root_node, file_path, imports, anomalies):
        if file_path.endswith("__init__.py"):
            return
        used_names: set[str] = set()
        for node in self._walk_all(root_node):
            if node.type == "identifier":
                used_names.add(node.text.decode())
            elif node.type == "type":
                for child in node.children:
                    if child.type == "identifier":
                        used_names.add(child.text.decode())
        for imp in imports:
            local_name = None
            if " as " in imp:
                local_name = imp.split(" as ")[-1].strip()
            elif imp.startswith("from ") and " import " in imp:
                parts = imp.split(" import ")[-1]
                local_name = parts.split(",")[0].strip()
            else:
                local_name = imp.split(".")[-1].strip()
            if local_name and local_name not in used_names:
                self._add_anomaly(anomalies, "unused_import", "low", file_path, 0, f"Import '{imp}' appears unused")

    def _check_global_mutations(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type == "global_statement":
                names = []
                for child in node.children:
                    if child.type == "identifier":
                        names.append(child.text.decode())
                self._add_anomaly(anomalies, "global_mutation", "medium", file_path, self._node_line(node), f"Global mutation of: {', '.join(names)}")

    def _check_missing_init(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "class_definition":
                continue
            has_init = False
            has_methods = False
            for child in node.body_children if hasattr(node, 'body_children') else node.children:
                if child.type in ("function_definition", "async_function_definition"):
                    name = self._get_node_name(child)
                    if name == "__init__":
                        has_init = True
                    elif name and not name.startswith("_"):
                        has_methods = True
            if has_methods and not has_init:
                cls_name = self._get_node_name(node)
                self._add_anomaly(anomalies, "missing_init", "low", file_path, self._node_line(node), f"Class {cls_name} has methods but no __init__")

    def _check_star_imports(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "import_from_statement":
                continue
            for child in node.children:
                if child.type == "dotted_name":
                    text = child.text.decode()
                    break
            else:
                continue
            is_star = False
            prev_was_import = False
            for child in node.children:
                if prev_was_import and child.type == "*":
                    is_star = True
                if child.type == "import":
                    prev_was_import = True
            if is_star:
                self._add_anomaly(anomalies, "star_import", "medium", file_path, self._node_line(node), f"Star import from '{text}' — pollutes namespace")

    def _check_eval_exec(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "call":
                continue
            func_name = None
            for child in node.children:
                if child.type == "identifier":
                    func_name = child.text.decode()
                    break
            if func_name in ("eval", "exec"):
                self._add_anomaly(anomalies, "eval_exec", "high", file_path, self._node_line(node), f"Use of {func_name}() — potential code injection risk")

    @staticmethod
    def _python_assign_target(node) -> str | None:
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode()
            elif child.type == "attribute":
                parts = []
                n = child
                while n.type == "attribute":
                    for c in n.children:
                        if c.type == "identifier" and c.prev_sibling and c.prev_sibling.type == ".":
                            parts.append(c.text.decode())
                    n = None
                    for c in child.children:
                        if c.type == "attribute":
                            n = c
                            break
                parts.reverse()
                return ".".join(parts)
        return None

    @staticmethod
    def _python_value_is_string(node):
        for child in node.children:
            if child.type == "string":
                return True
        return False