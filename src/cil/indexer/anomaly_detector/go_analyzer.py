from cil.indexer.anomaly_detector.base import BaseAnalyzer
from cil.indexer.anomaly_detector.utils import (
    check_long_functions,
    check_deep_nesting,
    check_hardcoded_secrets,
)
from cil.indexer.ast_parser import _get_parser

class GoAnalyzer(BaseAnalyzer):
    FUNC_TYPES = ("function_declaration", "method_declaration")
    NESTING_TYPES = ("if_statement", "for_statement", "switch_statement", "select_statement")

    def analyze(self, file_path: str, symbols: list, imports: list[str]) -> list[dict]:
        anomalies: list[dict] = []
        parser = _get_parser(".go")
        if not parser:
            return anomalies
        with open(file_path, "rb") as f:
            source = f.read()
        tree = parser.parse(source)
        root_node = tree.root_node
        self._check_unchecked_error(root_node, file_path, anomalies)
        check_long_functions(root_node, file_path, anomalies, self.FUNC_TYPES)
        check_deep_nesting(root_node, file_path, anomalies, self.NESTING_TYPES)
        self._check_blank_identifier_error(root_node, file_path, anomalies)
        check_hardcoded_secrets(root_node, file_path, anomalies, "short_var_declaration", self._go_assign_target)
        return anomalies

    def _get_lhs_targets(self, node):
        targets = []
        first_expr_list = None
        for child in node.children:
            if child.type == "expression_list":
                if first_expr_list is None:
                    first_expr_list = child
                else:
                    break
        if first_expr_list:
            for child in first_expr_list.children:
                if child.type == "identifier":
                    targets.append(child.text.decode())
        return targets

    def _check_unchecked_error(self, root_node, file_path, anomalies):
        # Collect LHS targets including blank identifiers for short_var_declaration
        def _get_lhs_with_blanks(node):
            first_expr_list = None
            for child in node.children:
                if child.type == "expression_list":
                    if first_expr_list is None:
                        first_expr_list = child
                    else:
                        break
            if not first_expr_list:
                return []
            targets = []
            for child in first_expr_list.children:
                if child.type == "blank_identifier":
                    targets.append("_")
                elif child.type == "identifier":
                    targets.append(child.text.decode())
            return targets

        # Pattern 1: short_var_declaration (:=)
        for node in self._walk_all(root_node):
            if node.type != "short_var_declaration":
                continue
            lhs_targets = _get_lhs_with_blanks(node)
            if not lhs_targets:
                continue
            has_err = any(t.endswith("err") or t == "error" for t in lhs_targets)
            non_blanks = [t for t in lhs_targets if t != "_"]
            all_blank = all(t == "_" for t in lhs_targets)
            # All-blank: _ := func() — error discarded via blank identifier
            if all_blank and len(lhs_targets) >= 1:
                self._add_anomaly(anomalies, "unchecked_error", "high", file_path, self._node_line(node), "Error value discarded with blank identifier '_' — consider logging or handling the error")
            # Multiple non-blank vars without err variable
            elif not has_err and len(non_blanks) >= 2:
                self._add_anomaly(anomalies, "unchecked_error", "medium", file_path, self._node_line(node), f"Potential unchecked error — variables {', '.join(non_blanks)} may be from a function that returns an error")

        # Pattern 2: assignment_statement where LHS contains only blank identifiers (_ = func())
        for node in self._walk_all(root_node):
            if node.type != "assignment_statement":
                continue
            # In tree-sitter-go, _ in assignments appears as identifier("_") inside expression_list
            lhs_expr_list = None
            for child in node.children:
                if child.type == "expression_list":
                    if lhs_expr_list is None:
                        lhs_expr_list = child
                    else:
                        break
            if not lhs_expr_list:
                continue
            lhs_items = []
            for child in lhs_expr_list.children:
                if child.type == "identifier":
                    lhs_items.append(child.text.decode())
                elif child.type == "blank_identifier":
                    lhs_items.append("_")
            if lhs_items and all(t == "_" for t in lhs_items):
                self._add_anomaly(anomalies, "unchecked_error", "high", file_path, self._node_line(node), "Error value discarded with blank identifier '_' — consider logging or handling the error")

        # Pattern 3: expression_statement containing a direct call_expression (dropped return values)
        for node in self._walk_all(root_node):
            if node.type != "expression_statement":
                continue
            for child in node.children:
                if child.type == "call_expression":
                    self._add_anomaly(anomalies, "unchecked_error", "high", file_path, self._node_line(node), "Function result dropped — potential unchecked error; assign to variable or explicitly discard with '_'")
                    break

    def _check_blank_identifier_error(self, root_node, file_path, anomalies):
        for node in self._walk_all(root_node):
            if node.type != "short_var_declaration":
                continue
            targets = self._get_lhs_targets(node)
            if "_" not in targets:
                continue
            has_error_type = False
            for child in self._walk_all(node):
                if child.type == "type_identifier" and child.text.decode() == "error":
                    has_error_type = True
            if has_error_type:
                self._add_anomaly(anomalies, "blank_identifier_error", "low", file_path, self._node_line(node), "Error value discarded with blank identifier '_' — consider logging or handling the error")

    @staticmethod
    def _go_assign_target(node):
        names = []
        first_expr_list = None
        for child in node.children:
            if child.type == "expression_list":
                if first_expr_list is None:
                    first_expr_list = child
                else:
                    break
        if first_expr_list:
            for child in first_expr_list.children:
                if child.type == "identifier":
                    names.append(child.text.decode())
        return ", ".join(names) if names else None