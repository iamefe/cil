import ast
import os
from typing import Optional

from cil.models import SymbolInfo, CallEdge, MutationInfo, FileIndex


class ASTParser:
    """Parse Python files using the ast module and extract symbols, call graph, and mutations."""

    def __init__(self):
        self.symbols: list[SymbolInfo] = []
        self.calls: list[CallEdge] = []
        self.mutations: list[MutationInfo] = []
        self.imports: list[str] = []

    def parse_file(self, file_path: str) -> FileIndex:
        """Parse a single Python file and return its index."""
        self.symbols = []
        self.calls = []
        self.mutations = []
        self.imports = []

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError as e:
            print(f"Warning: syntax error in {file_path}:{e.lineno} — {e.msg}")
            return FileIndex(file_path=file_path)

        # Extract top-level imports first
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                self._handle_import(node)
            elif isinstance(node, ast.ImportFrom):
                self._handle_import_from(node)

        self._walk(tree, file_path, parent_name="<module>")

        return FileIndex(
            file_path=file_path,
            symbols=self.symbols,
            imports=self.imports,
        )

    def _walk(self, node: ast.AST, file_path: str, parent_name: str):
        """Walk the AST and extract information."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._handle_function(child, file_path)
            elif isinstance(child, ast.ClassDef):
                self._handle_class(child, file_path)
            elif isinstance(child, ast.Assign):
                self._handle_assign(child, file_path, parent_name)
            elif isinstance(child, ast.AugAssign):
                self._handle_aug_assign(child, file_path, parent_name)
            elif isinstance(child, ast.Delete):
                self._handle_delete(child, file_path, parent_name)

            # Recurse with updated parent context
            new_parent = parent_name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                new_parent = child.name

            self._walk(child, file_path, new_parent)

    def _handle_import(self, node: ast.Import):
        for alias in node.names:
            name = alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"
            self.imports.append(name)

    def _handle_import_from(self, node: ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            name = alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"
            self.imports.append(f"from {module} import {name}")

    def _handle_function(self, node, file_path: str):
        sig = self._build_signature(node)
        docstring = ast.get_docstring(node) or ""
        decorators = [self._decorator_name(d) for d in node.decorator_list]

        self.symbols.append(SymbolInfo(
            name=node.name,
            kind="function",
            file_path=file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            signature=sig,
            docstring=docstring,
            decorators=decorators,
        ))

        # Extract calls within this function
        caller_ref = f"{file_path}:{node.lineno}:{node.name}"
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee_name = self._call_name(child.func)
                if callee_name:
                    self.calls.append(CallEdge(
                        caller=caller_ref,
                        callee=callee_name,
                        line=child.lineno,
                    ))

    def _handle_class(self, node: ast.ClassDef, file_path: str):
        docstring = ast.get_docstring(node) or ""
        decorators = [self._decorator_name(d) for d in node.decorator_list]

        self.symbols.append(SymbolInfo(
            name=node.name,
            kind="class",
            file_path=file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            signature=f"class {node.name}",
            docstring=docstring,
            decorators=decorators,
        ))

    def _handle_assign(self, node: ast.Assign, file_path: str, parent_name: str):
        for target in node.targets:
            target_name = self._target_name(target)
            if target_name:
                self.mutations.append(MutationInfo(
                    target=target_name,
                    source=f"{file_path}:{node.lineno}:{parent_name}",
                    line=node.lineno,
                    kind="assign",
                ))

    def _handle_aug_assign(self, node: ast.AugAssign, file_path: str, parent_name: str):
        target_name = self._target_name(node.target)
        if target_name:
            self.mutations.append(MutationInfo(
                target=target_name,
                source=f"{file_path}:{node.lineno}:{parent_name}",
                line=node.lineno,
                kind="augment",
            ))

    def _handle_delete(self, node: ast.Delete, file_path: str, parent_name: str):
        for target in node.targets:
            target_name = self._target_name(target)
            if target_name:
                self.mutations.append(MutationInfo(
                    target=target_name,
                    source=f"{file_path}:{node.lineno}:{parent_name}",
                    line=node.lineno,
                    kind="delete",
                ))

    # --- Helpers ---

    @staticmethod
    def _build_signature(node) -> str:
        """Build a human-readable function signature."""
        args = []
        for arg in node.args.args:
            if arg.arg == "self":
                continue
            args.append(arg.arg)

        if node.returns:
            ret = ast.unparse(node.returns)
        else:
            ret = ""

        return f"def {node.name}({', '.join(args)}) -> {ret}".strip(" -> ")

    @staticmethod
    def _decorator_name(node) -> str:
        """Extract decorator name."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Call):
            return ASTParser._decorator_name(node.func)
        if isinstance(node, ast.Attribute):
            return node.attr
        return ast.unparse(node)

    @staticmethod
    def _call_name(node) -> Optional[str]:
        """Extract the name of a function call."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def _target_name(node) -> Optional[str]:
        """Extract the name of an assignment target."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{ASTParser._target_name(node.value)}.{node.attr}" if ASTParser._target_name(node.value) else node.attr
        if isinstance(node, ast.Subscript):
            val = ASTParser._target_name(node.value)
            if val:
                return f"{val}[...]"
        return None

