import re
from cil.indexer.anomaly_detector.base import BaseAnalyzer
from cil.indexer.anomaly_detector.utils import (
    check_long_functions,
    check_deep_nesting,
    check_hardcoded_secrets,
)
from cil.indexer.ast_parser import _get_parser

class TypeScriptAnalyzer(BaseAnalyzer):
    FUNC_TYPES = ("function_declaration", "method_definition", "arrow_function")
    NESTING_TYPES = ("if_statement", "for_statement", "while_statement", "for_in_statement", "switch_statement")

    def analyze(self, file_path: str, symbols: list, imports: list[str]) -> list[dict]:
        anomalies: list[dict] = []
        parser = _get_parser(".ts")
        if not parser:
            return anomalies
        with open(file_path, "rb") as f:
            source = f.read()
        tree = parser.parse(source)
        root_node = tree.root_node
        self._check_empty_catch(root_node, file_path, anomalies)
        self._check_any_type(root_node, file_path, anomalies)
        check_long_functions(root_node, file_path, anomalies, self.FUNC_TYPES)
        check_deep_nesting(root_node, file_path, anomalies, self.NESTING_TYPES)
        self._check_eval_usage(root_node, file_path, anomalies)
        self._check_console_logging(root_node, file_path, anomalies)
        self._check_unused_imports(root_node, file_path, imports, anomalies)
        self._check_wildcard_import(root_node, file_path, anomalies)
        check_hardcoded_secrets(root_node, file_path, anomalies, "variable_declarator", self._ts_assign_target)
        return anomalies

    def _check_empty_catch(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "catch_clause":
                continue
            body = None
            for child in node.children:
                if child.type == "statement_block":
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
                self._add_anomaly(anomalies, "empty_catch", "medium", file_path, self._node_line(node), "Empty catch clause — errors are silently swallowed")

    def _check_any_type(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "type_annotation":
                continue
            for child in self._walk_all(node):
                if child.type == "any":
                    self._add_anomaly(anomalies, "any_type", "low", file_path, self._node_line(node), "Use of 'any' type — defeats TypeScript's type safety")
                    break

    def _check_eval_usage(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "call_expression":
                continue
            func_name = None
            for child in node.children:
                if child.type == "identifier":
                    func_name = child.text.decode()
                    break
            if func_name == "eval":
                self._add_anomaly(anomalies, "eval_usage", "high", file_path, self._node_line(node), "Use of eval() — potential code injection risk")

    def _check_console_logging(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "call_expression":
                continue
            member_expr = None
            for child in node.children:
                if child.type == "member_expression":
                    member_expr = child
                    break
            if not member_expr:
                continue
            obj = None
            prop = None
            for child in member_expr.children:
                if child.type == "identifier" and child.next_sibling and child.next_sibling.type == ".":
                    obj = child.text.decode()
                elif child.type == "property_identifier" and child.prev_sibling and child.prev_sibling.type == ".":
                    prop = child.text.decode()
            if obj == "console":
                self._add_anomaly(anomalies, "console_logging", "low", file_path, self._node_line(node), f"Console logging (console.{prop}) — remove before production")

    def _check_unused_imports(self, root_node, file_path, imports, anomalies):
        used_names: set[str] = set()
        for node in self._walk_all(root_node):
            if node.type == "identifier":
                used_names.add(node.text.decode())
            elif node.type == "type_identifier":
                used_names.add(node.text.decode())
        for imp in imports:
            local_name = None
            if " as " in imp:
                local_name = imp.split(" as ")[-1].strip()
            else:
                parts = imp.split(" import ")[-1]
                name_part = re.sub(r'[{}]', '', parts).split(",")[0].strip()
                local_name = name_part.split("/")[-1].strip()
            if local_name and local_name not in used_names:
                self._add_anomaly(anomalies, "unused_import", "low", file_path, 0, f"Import '{imp}' appears unused")

    def _check_wildcard_import(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "import_statement":
                continue
            has_star = False
            source_text = ""
            for child in node.children:
                if child.type == "namespace_import":
                    has_star = True
                if child.type == "string":
                    source_text = child.text.decode().strip("'\"")
            if has_star:
                self._add_anomaly(anomalies, "wildcard_import", "medium", file_path, self._node_line(node), f"Wildcard/namespace import from '{source_text}' — pollutes namespace")

    @staticmethod
    def _ts_assign_target(node) -> str | None:
        return BaseAnalyzer._get_node_name(node)