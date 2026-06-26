import ast
from typing import Optional

from cil.models import SymbolInfo


class AnomalyDetector:
    """Static analysis patterns that detect common code anomalies without an LLM."""

    def __init__(self):
        self.anomalies: list[dict] = []

    def analyze_file(self, file_path: str, symbols: list[SymbolInfo], imports: list[str]) -> list[dict]:
        """Run all anomaly checks on a file. Returns list of anomaly dicts."""
        self.anomalies = []

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return []

        self._check_bare_except(tree, file_path)
        self._check_bare_raise(tree, file_path)
        self._check_mutable_defaults(tree, file_path)
        self._check_long_functions(tree, file_path)
        self._check_deep_nesting(tree, file_path)
        self._check_resource_leaks(tree, file_path)
        self._check_unused_imports(tree, file_path, imports)
        self._check_global_mutations(tree, file_path)
        self._check_missing_init(tree, file_path)
        self._check_star_imports(tree, file_path)
        self._check_eval_exec(tree, file_path)
        self._check_hardcoded_secrets(tree, file_path)

        return self.anomalies

    # --- Individual checks ---

    def _check_bare_except(self, tree: ast.AST, file_path: str):
        """Detect bare except clauses (except: without exception type)."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                self.anomalies.append({
                    "type": "bare_except",
                    "severity": "high",
                    "file_path": file_path,
                    "line": node.lineno,
                    "message": "Bare except clause catches all exceptions including KeyboardInterrupt and SystemExit",
                })

    def _check_bare_raise(self, tree: ast.AST, file_path: str):
        """Detect bare raise (re-raise without context) in except handlers."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                for child in ast.walk(node):
                    if isinstance(child, ast.Raise) and child.exc is None:
                        self.anomalies.append({
                            "type": "bare_raise",
                            "severity": "medium",
                            "file_path": file_path,
                            "line": child.lineno,
                            "message": "Bare raise without 'raise' keyword — may lose exception context",
                        })

    def _check_mutable_defaults(self, tree: ast.AST, file_path: str):
        """Detect mutable default arguments (list, dict, set)."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults + node.args.kw_defaults:
                    if default and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        self.anomalies.append({
                            "type": "mutable_default",
                            "severity": "high",
                            "file_path": file_path,
                            "line": node.lineno,
                            "message": f"Mutable default argument in {node.name}() — shared across all calls",
                        })

    def _check_long_functions(self, tree: ast.AST, file_path: str):
        """Detect functions longer than 80 lines."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.end_lineno and (node.end_lineno - node.lineno) > 80:
                    length = node.end_lineno - node.lineno
                    self.anomalies.append({
                        "type": "long_function",
                        "severity": "medium",
                        "file_path": file_path,
                        "line": node.lineno,
                        "message": f"Function {node.name}() is {length} lines — consider splitting",
                    })

    def _check_deep_nesting(self, tree: ast.AST, file_path: str):
        """Detect nesting depth > 4."""
        nesting_types = (ast.If, ast.For, ast.While, ast.With, ast.Try)

        def _check_depth(node, depth):
            if depth > 4:
                self.anomalies.append({
                    "type": "deep_nesting",
                    "severity": "medium",
                    "file_path": file_path,
                    "line": node.lineno,
                    "message": f"Nesting depth {depth} — consider extracting to a function",
                })
            for child in ast.iter_child_nodes(node):
                if isinstance(child, nesting_types):
                    _check_depth(child, depth + 1)

        _check_depth(tree, 0)

    def _check_resource_leaks(self, tree: ast.AST, file_path: str):
        """Detect open() calls not inside a with statement."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = self._get_func_name(node.func)
                if func_name == "open":
                    # Check if this call is inside a with statement
                    if not self._is_inside_with(node, tree):
                        self.anomalies.append({
                            "type": "resource_leak",
                            "severity": "high",
                            "file_path": file_path,
                            "line": node.lineno,
                            "message": "open() without context manager — file may not be closed on error",
                        })

    def _check_unused_imports(self, tree: ast.AST, file_path: str, imports: list[str]):
        """Detect imports that are never referenced in the code."""
        # Skip __init__.py — they often re-export
        if file_path.endswith("__init__.py"):
            return

        # Collect all Name nodes used in the file (excluding import statements)
        used_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used_names.add(node.id)
            # Exception handler types (except SomeError:)
            if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name):
                used_names.add(node.type.id)
            # Star args and kwargs
            if isinstance(node, ast.Starred) and isinstance(node.value, ast.Name):
                used_names.add(node.value.id)
            if isinstance(node, ast.Attribute):
                # Walk up to get the root name
                val = node.value
                while isinstance(val, ast.Attribute):
                    val = val.value
                if isinstance(val, ast.Name):
                    used_names.add(val.id)
            # Decorator names
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name):
                        used_names.add(dec.id)
                    elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                        used_names.add(dec.func.id)
            # Type annotations
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns and isinstance(node.returns, ast.Name):
                    used_names.add(node.returns.id)
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.annotation and isinstance(arg.annotation, ast.Name):
                        used_names.add(arg.annotation.id)

        # Check each import
        for imp in imports:
            # Extract the local name (after 'as' or the last part)
            if " as " in imp:
                local_name = imp.split(" as ")[-1].strip()
            elif imp.startswith("from ") and " import " in imp:
                # "from X import Y" -> extract Y
                parts = imp.split(" import ")[-1]
                local_name = parts.split(",")[0].strip()
            else:
                local_name = imp.split(".")[-1].strip()

            if local_name and local_name not in used_names:
                self.anomalies.append({
                    "type": "unused_import",
                    "severity": "low",
                    "file_path": file_path,
                    "line": 0,
                    "message": f"Import '{imp}' appears unused",
                })

    def _check_global_mutations(self, tree: ast.AST, file_path: str):
        """Detect global keyword usage (mutating module-level state)."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                self.anomalies.append({
                    "type": "global_mutation",
                    "severity": "medium",
                    "file_path": file_path,
                    "line": node.lineno,
                    "message": f"Global mutation of: {', '.join(node.names)}",
                })

    def _check_missing_init(self, tree: ast.AST, file_path: str):
        """Detect classes with methods but no __init__."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                has_init = any(
                    isinstance(m, ast.FunctionDef) and m.name == "__init__"
                    for m in node.body
                )
                has_methods = any(
                    isinstance(m, ast.FunctionDef) and not m.name.startswith("_")
                    for m in node.body
                )
                if has_methods and not has_init:
                    self.anomalies.append({
                        "type": "missing_init",
                        "severity": "low",
                        "file_path": file_path,
                        "line": node.lineno,
                        "message": f"Class {node.name} has methods but no __init__",
                    })

    def _check_star_imports(self, tree: ast.AST, file_path: str):
        """Detect 'from x import *' statements."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                self.anomalies.append({
                    "type": "star_import",
                    "severity": "medium",
                    "file_path": file_path,
                    "line": node.lineno,
                    "message": f"Star import from '{node.module}' — pollutes namespace",
                })

    def _check_eval_exec(self, tree: ast.AST, file_path: str):
        """Detect eval() and exec() calls."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = self._get_func_name(node.func)
                if func_name in ("eval", "exec"):
                    self.anomalies.append({
                        "type": "eval_exec",
                        "severity": "high",
                        "file_path": file_path,
                        "line": node.lineno,
                        "message": f"Use of {func_name}() — potential code injection risk",
                    })

    def _check_hardcoded_secrets(self, tree: ast.AST, file_path: str):
        """Detect potential hardcoded secrets (password, secret, key, token in variable names)."""
        secret_keywords = {"password", "secret", "key", "token", "api_key", "apikey", "api_secret"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    name = self._get_assign_name(target)
                    if name and any(kw in name.lower() for kw in secret_keywords):
                        # Check if the value is a string literal
                        if isinstance(node.value, ast.Constant):
                            self.anomalies.append({
                                "type": "hardcoded_secret",
                                "severity": "high",
                                "file_path": file_path,
                                "line": node.lineno,
                                "message": f"Potential hardcoded secret in variable '{name}'",
                            })

    # --- Helpers ---

    @staticmethod
    def _get_func_name(node) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def _get_assign_name(node) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            val = AnomalyDetector._get_assign_name(node.value)
            return f"{val}.{node.attr}" if val else node.attr
        return None

    @staticmethod
    def _is_inside_with(node: ast.AST, tree: ast.AST) -> bool:
        """Check if a node is inside a with statement by walking the tree."""
        for parent in ast.walk(tree):
            if isinstance(parent, ast.With):
                for item in ast.walk(parent):
                    if item is node:
                        return True
        return False
