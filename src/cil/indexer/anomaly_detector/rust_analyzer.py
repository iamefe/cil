from cil.indexer.anomaly_detector.base import BaseAnalyzer
from cil.indexer.anomaly_detector.utils import (
    check_long_functions,
    check_deep_nesting,
    check_hardcoded_secrets,
)
from cil.indexer.ast_parser import _get_parser

class RustAnalyzer(BaseAnalyzer):
    FUNC_TYPES = ("function_item",)
    NESTING_TYPES = ("if_expression", "while_expression", "for_expression", "loop_expression")

    def analyze(self, file_path: str, symbols: list, imports: list[str]) -> list[dict]:
        anomalies: list[dict] = []
        parser = _get_parser(".rs")
        if not parser:
            return anomalies
        with open(file_path, "rb") as f:
            source = f.read()
        tree = parser.parse(source)
        root_node = tree.root_node
        self._check_unwrap_call(root_node, file_path, anomalies)
        self._check_expect_no_message(root_node, file_path, anomalies)
        self._check_unsafe_block(root_node, file_path, anomalies)
        self._check_panic_macro(root_node, file_path, anomalies)
        check_long_functions(root_node, file_path, anomalies, self.FUNC_TYPES)
        check_hardcoded_secrets(root_node, file_path, anomalies, "let_declaration", self._rust_assign_target)
        return anomalies

    def _check_unwrap_call(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "call_expression":
                continue
            for child in self._walk_all(node):
                if child.type == "field_identifier" and child.text.decode() == "unwrap":
                    self._add_anomaly(anomalies, "unwrap_call", "high", file_path, self._node_line(node), "Use of unwrap() — will panic on Err/None; use expect() with a message or handle the error")
                    break

    def _check_expect_no_message(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "call_expression":
                continue
            is_expect = False
            has_meaningful_msg = False
            for child in self._walk_all(node):
                if child.type == "field_identifier" and child.text.decode() == "expect":
                    is_expect = True
                if child.type == "string_literal":
                    raw = child.text.decode()
                    if len(raw.strip()) > 2:
                        has_meaningful_msg = True
            if is_expect and not has_meaningful_msg:
                self._add_anomaly(anomalies, "expect_no_message", "medium", file_path, self._node_line(node), "expect() without meaningful message — provide context for debugging")

    def _check_unsafe_block(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type == "unsafe_block":
                self._add_anomaly(anomalies, "unsafe_block", "low", file_path, self._node_line(node), "Unsafe block — verify memory safety guarantees are maintained")

    def _check_panic_macro(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "macro_invocation":
                continue
            macro_name = None
            for child in node.children:
                if child.type == "identifier" and child.next_sibling and child.next_sibling.type == "!":
                    macro_name = child.text.decode()
                    break
            if macro_name == "panic":
                self._add_anomaly(anomalies, "panic_macro", "high", file_path, self._node_line(node), "Use of panic! macro — consider returning a Result instead")

    @staticmethod
    def _rust_assign_target(node) -> str | None:
        pattern = BaseAnalyzer._find_child(node, "let_declaration")
        if not pattern:
            pattern = node
        ident = None
        for child in pattern.children:
            if child.type == "identifier":
                ident = child.text.decode()
                break
        return ident