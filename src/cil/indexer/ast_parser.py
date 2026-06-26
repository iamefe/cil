import os
from typing import Optional

from tree_sitter import Language, Parser

from cil.models import SymbolInfo, CallEdge, MutationInfo, FileIndex

# Language registry: extension -> (language_module, language_func)
_LANGUAGE_REGISTRY: dict[str, tuple] = {}
_PARSER_CACHE: dict[str, Parser] = {}


def _get_language(ext: str) -> Optional[Language]:
    """Get the tree-sitter Language for a file extension."""
    if ext in _PARSER_CACHE:
        return _PARSER_CACHE[ext].language

    lang = None
    if ext in (".py", ".pyi"):
        import tree_sitter_python as tspython
        lang = Language(tspython.language())
    elif ext in (".ts", ".tsx"):
        import tree_sitter_typescript as tstypescript
        lang = Language(tstypescript.language_typescript())
    elif ext in (".js", ".jsx"):
        import tree_sitter_javascript as tsjs
        lang = Language(tsjs.language())
    elif ext in (".go",):
        import tree_sitter_go as tsgo
        lang = Language(tsgo.language())
    elif ext in (".rs",):
        import tree_sitter_rust as tsrust
        lang = Language(tsrust.language())
    elif ext in (".java",):
        import tree_sitter_java as tsjava
        lang = Language(tsjava.language())
    elif ext in (".c", ".h"):
        import tree_sitter_c as tsc
        lang = Language(tsc.language())

    if lang:
        _PARSER_CACHE[ext] = Parser(lang)
    return lang


def _get_parser(ext: str) -> Optional[Parser]:
    """Get or create a cached Parser for a file extension."""
    if ext not in _PARSER_CACHE:
        _get_language(ext)
    return _PARSER_CACHE.get(ext)


class ASTParser:
    """Parse source files using tree-sitter and extract symbols, call graph, and mutations."""

    # Node types per language family
    PYTHON_FUNCTION = "function_definition"
    PYTHON_CLASS = "class_definition"
    PYTHON_IMPORT = "import_statement"
    PYTHON_IMPORT_FROM = "import_from_statement"
    PYTHON_CALL = "call"
    PYTHON_ASSIGN = "assignment"
    PYTHON_AUG_ASSIGN = "augmented_assignment"
    PYTHON_DELETE = "delete_statement"
    PYTHON_RETURN = "return_statement"

    TS_FUNCTION = "function_declaration"
    TS_CLASS = "class_declaration"
    TS_IMPORT = "import_statement"
    TS_CALL = "call_expression"
    TS_ASSIGN = "assignment_expression"
    TS_AUG_ASSIGN = "augmented_assignment_expression"
    TS_RETURN = "return_statement"
    TS_LEXICAL = "lexical_declaration"
    TS_VARIABLE = "variable_declaration"
    TS_ARROW = "arrow_function"
    TS_METHOD = "method_definition"
    TS_PROPERTY = "public_field_definition"
    TS_INTERFACE = "interface_declaration"
    TS_TYPE_ALIAS = "type_alias_declaration"
    TS_EXPORT = "export_statement"

    JS_FUNCTION = "function_declaration"
    JS_CLASS = "class_declaration"
    JS_IMPORT = "import_statement"
    JS_CALL = "call_expression"
    JS_ASSIGN = "assignment_expression"
    JS_AUG_ASSIGN = "augmented_assignment_expression"
    JS_RETURN = "return_statement"
    JS_LEXICAL = "lexical_declaration"
    JS_VARIABLE = "variable_declaration"
    JS_ARROW = "arrow_function"
    JS_METHOD = "method_definition"
    JS_PROPERTY = "public_field_definition"
    JS_EXPORT = "export_statement"

    # Go node types
    GO_FUNCTION = "function_declaration"
    GO_METHOD = "method_declaration"
    GO_STRUCT = "type_declaration"
    GO_IMPORT = "import_declaration"
    GO_CALL = "call_expression"
    GO_ASSIGN = "short_var_declaration"
    GO_RETURN = "return_statement"
    GO_CONST = "const_declaration"

    # Rust node types
    RUST_FUNCTION = "function_item"
    RUST_STRUCT = "struct_item"
    RUST_IMPL = "impl_item"
    RUST_IMPORT = "use_declaration"
    RUST_CALL = "call_expression"
    RUST_ASSIGN = "let_declaration"
    RUST_RETURN = "return_expression"
    RUST_CONST = "const_item"
    RUST_MACRO = "macro_invocation"

    # Java node types
    JAVA_FUNCTION = "method_declaration"
    JAVA_CONSTRUCTOR = "constructor_declaration"
    JAVA_CLASS = "class_declaration"
    JAVA_IMPORT = "import_declaration"
    JAVA_CALL = "method_invocation"
    JAVA_ASSIGN = "assignment_expression"
    JAVA_RETURN = "return_statement"
    JAVA_VAR = "local_variable_declaration"

    # C node types
    C_FUNCTION = "function_definition"
    C_STRUCT = "type_definition"
    C_IMPORT = "preproc_include"
    C_CALL = "call_expression"
    C_ASSIGN = "assignment_expression"
    C_RETURN = "return_statement"
    C_VAR = "declaration"
    C_DEFINE = "preproc_def"

    def __init__(self):
        self.symbols: list[SymbolInfo] = []
        self.calls: list[CallEdge] = []
        self.mutations: list[MutationInfo] = []
        self.imports: list[str] = []

    def parse_file(self, file_path: str) -> FileIndex:
        """Parse a single file and return its index."""
        self.symbols = []
        self.calls = []
        self.mutations = []
        self.imports = []

        ext = os.path.splitext(file_path)[1]
        parser = _get_parser(ext)
        if not parser:
            return FileIndex(file_path=file_path)

        with open(file_path, "rb") as f:
            source = f.read()

        try:
            tree = parser.parse(source)
        except Exception as e:
            print(f"Warning: parse error in {file_path}: {e}")
            return FileIndex(file_path=file_path)

        if tree.root_node.has_error:
            print(f"Warning: syntax errors in {file_path}")

        self._extract_imports(tree.root_node, ext)
        self._walk(tree.root_node, file_path, ext, parent_name="<module>")

        return FileIndex(
            file_path=file_path,
            symbols=self.symbols,
            imports=self.imports,
        )

    # --- Import extraction ---

    def _extract_imports(self, root, ext: str):
        """Extract top-level import statements."""
        for child in root.children:
            if ext in (".py", ".pyi"):
                self._extract_python_import(child)
            elif ext in (".ts", ".tsx", ".js", ".jsx"):
                self._extract_js_import(child)
            elif ext in (".go",):
                self._extract_go_import(child)
            elif ext in (".rs",):
                self._extract_rust_import(child)
            elif ext in (".java",):
                self._extract_java_import(child)
            elif ext in (".c", ".h"):
                self._extract_c_import(child)

    def _extract_python_import(self, node):
        if node.type == self.PYTHON_IMPORT:
            for child in node.children:
                if child.type == "dotted_name":
                    self.imports.append(child.text.decode())
        elif node.type == self.PYTHON_IMPORT_FROM:
            module = ""
            for child in node.children:
                if child.type == "dotted_name" and child.prev_sibling and child.prev_sibling.type == "from":
                    module = child.text.decode()
                elif child.type == "dotted_name" and child.prev_sibling and child.prev_sibling.type == "import":
                    self.imports.append(f"from {module} import {child.text.decode()}")

    def _extract_js_import(self, node):
        if node.type not in (self.TS_IMPORT, self.JS_IMPORT):
            return
        # Find the source string
        source = ""
        for child in node.children:
            if child.type == "string":
                source = child.text.decode().strip("'\"")

        # Find the import clause
        for child in node.children:
            if child.type == "import_clause":
                # Named imports: { foo, bar }
                for c in child.children:
                    if c.type == "named_imports":
                        for spec in c.children:
                            if spec.type == "import_specifier":
                                for s in spec.children:
                                    if s.type in ("identifier", "type_identifier"):
                                        name = s.text.decode()
                                        self.imports.append(f"from {source} import {name}")
                    elif c.type in ("identifier", "type_identifier"):
                        name = c.text.decode()
                        self.imports.append(f"from {source} import {name}")

    def _extract_go_import(self, node):
        if node.type != self.GO_IMPORT:
            return
        for child in node.children:
            if child.type == "import_spec":
                for c in child.children:
                    if c.type == "interpreted_string_literal":
                        self.imports.append(c.text.decode().strip('"'))
            elif child.type == "import_spec_list":
                for c in child.children:
                    if c.type == "interpreted_string_literal":
                        self.imports.append(c.text.decode().strip('"'))

    def _extract_rust_import(self, node):
        if node.type != self.RUST_IMPORT:
            return
        for child in node.children:
            if child.type == "scoped_identifier":
                self.imports.append(child.text.decode())
            elif child.type == "identifier":
                self.imports.append(child.text.decode())

    def _extract_java_import(self, node):
        if node.type != self.JAVA_IMPORT:
            return
        for child in node.children:
            if child.type == "scoped_identifier":
                self.imports.append(child.text.decode())

    def _extract_c_import(self, node):
        if node.type != self.C_IMPORT:
            return
        for child in node.children:
            if child.type in ("system_lib_string", "string_literal"):
                self.imports.append(child.text.decode().strip("<>").strip('"'))

    # --- Walking ---

    def _walk(self, node, file_path: str, ext: str, parent_name: str):
        """Walk the AST and extract information."""
        for child in node.children:
            if ext in (".py", ".pyi"):
                self._walk_python(child, file_path, parent_name)
            elif ext in (".ts", ".tsx", ".js", ".jsx"):
                self._walk_js(child, file_path, parent_name)
            elif ext in (".go",):
                self._walk_go(child, file_path, parent_name)
            elif ext in (".rs",):
                self._walk_rust(child, file_path, parent_name)
            elif ext in (".java",):
                self._walk_java(child, file_path, parent_name)
            elif ext in (".c", ".h"):
                self._walk_c(child, file_path, parent_name)

    def _walk_python(self, node, file_path: str, parent_name: str):
        new_parent = parent_name

        if node.type == self.PYTHON_FUNCTION:
            self._handle_python_function(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.PYTHON_CLASS:
            self._handle_python_class(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.PYTHON_ASSIGN:
            self._handle_python_assign(node, file_path, parent_name)
        elif node.type == self.PYTHON_AUG_ASSIGN:
            self._handle_python_aug_assign(node, file_path, parent_name)
        elif node.type == self.PYTHON_DELETE:
            self._handle_python_delete(node, file_path, parent_name)

        for child in node.children:
            self._walk_python(child, file_path, new_parent)

    def _walk_js(self, node, file_path: str, parent_name: str):
        new_parent = parent_name

        if node.type in (self.TS_FUNCTION, self.JS_FUNCTION):
            self._handle_js_function(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type in (self.TS_CLASS, self.JS_CLASS):
            self._handle_js_class(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type in (self.TS_INTERFACE,):
            self._handle_ts_interface(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type in (self.TS_TYPE_ALIAS,):
            self._handle_ts_type_alias(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type in (self.TS_LEXICAL, self.JS_LEXICAL, self.TS_VARIABLE, self.JS_VARIABLE):
            self._handle_js_variable(node, file_path, parent_name)
        elif node.type in (self.TS_ASSIGN, self.JS_ASSIGN):
            self._handle_js_assign(node, file_path, parent_name)
        elif node.type in (self.TS_AUG_ASSIGN, self.JS_AUG_ASSIGN):
            self._handle_js_aug_assign(node, file_path, parent_name)

        for child in node.children:
            self._walk_js(child, file_path, new_parent)

    # --- Python handlers ---

    def _handle_python_function(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        sig = self._build_python_signature(node)
        decorators = self._get_python_decorators(node)

        self.symbols.append(SymbolInfo(
            name=name,
            kind="function",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
            decorators=decorators,
        ))

        caller_ref = f"{file_path}:{node.start_point[0] + 1}:{name}"
        self._extract_python_calls(node, caller_ref)

    def _handle_python_class(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        decorators = self._get_python_decorators(node)

        self.symbols.append(SymbolInfo(
            name=name,
            kind="class",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"class {name}",
            decorators=decorators,
        ))

    def _handle_python_assign(self, node, file_path: str, parent_name: str):
        targets = self._get_python_assign_targets(node)
        for target in targets:
            self.mutations.append(MutationInfo(
                target=target,
                source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                line=node.start_point[0] + 1,
                kind="assign",
            ))

    def _handle_python_aug_assign(self, node, file_path: str, parent_name: str):
        target = self._get_python_aug_target(node)
        if target:
            self.mutations.append(MutationInfo(
                target=target,
                source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                line=node.start_point[0] + 1,
                kind="augment",
            ))

    def _handle_python_delete(self, node, file_path: str, parent_name: str):
        for child in node.children:
            if child.type == "expression_list":
                for c in child.children:
                    name = self._python_target_name(c)
                    if name:
                        self.mutations.append(MutationInfo(
                            target=name,
                            source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                            line=node.start_point[0] + 1,
                            kind="delete",
                        ))
            elif child.type in ("identifier", "attribute"):
                name = self._python_target_name(child)
                if name:
                    self.mutations.append(MutationInfo(
                        target=name,
                        source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                        line=node.start_point[0] + 1,
                        kind="delete",
                    ))

    # --- JS/TS handlers ---

    def _handle_js_function(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        sig = self._build_js_signature(node)

        self.symbols.append(SymbolInfo(
            name=name,
            kind="function",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
        ))

        caller_ref = f"{file_path}:{node.start_point[0] + 1}:{name}"
        self._extract_js_calls(node, caller_ref)

    def _handle_js_class(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        self.symbols.append(SymbolInfo(
            name=name,
            kind="class",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"class {name}",
        ))

    def _handle_ts_interface(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        self.symbols.append(SymbolInfo(
            name=name,
            kind="interface",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"interface {name}",
        ))

    def _handle_ts_type_alias(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        self.symbols.append(SymbolInfo(
            name=name,
            kind="type_alias",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"type {name}",
        ))

    def _handle_js_variable(self, node, file_path: str, parent_name: str):
        """Handle const/let/var declarations."""
        for child in node.children:
            if child.type == "variable_declarator":
                name = self._get_var_declarator_name(child)
                if name:
                    self.mutations.append(MutationInfo(
                        target=name,
                        source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                        line=node.start_point[0] + 1,
                        kind="assign",
                    ))

    def _handle_js_assign(self, node, file_path: str, parent_name: str):
        target = self._get_js_assign_target(node)
        if target:
            self.mutations.append(MutationInfo(
                target=target,
                source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                line=node.start_point[0] + 1,
                kind="assign",
            ))

    def _handle_js_aug_assign(self, node, file_path: str, parent_name: str):
        target = self._get_js_assign_target(node)
        if target:
            self.mutations.append(MutationInfo(
                target=target,
                source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                line=node.start_point[0] + 1,
                kind="augment",
            ))

    # --- Go handlers ---

    def _walk_go(self, node, file_path: str, parent_name: str):
        new_parent = parent_name

        if node.type == self.GO_FUNCTION:
            self._handle_go_function(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.GO_METHOD:
            self._handle_go_method(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.GO_STRUCT:
            self._handle_go_struct(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.GO_CONST:
            self._handle_go_const(node, file_path)
        elif node.type == self.GO_ASSIGN:
            self._handle_go_assign(node, file_path, parent_name)

        for child in node.children:
            self._walk_go(child, file_path, new_parent)

    def _handle_go_function(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        sig = self._build_go_signature(node)
        self.symbols.append(SymbolInfo(
            name=name,
            kind="function",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
        ))
        caller_ref = f"{file_path}:{node.start_point[0] + 1}:{name}"
        self._extract_go_calls(node, caller_ref)

    def _handle_go_method(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        sig = self._build_go_signature(node)
        self.symbols.append(SymbolInfo(
            name=name,
            kind="method",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
        ))
        caller_ref = f"{file_path}:{node.start_point[0] + 1}:{name}"
        self._extract_go_calls(node, caller_ref)

    def _handle_go_struct(self, node, file_path: str):
        for child in node.children:
            if child.type == "type_spec":
                name = self._get_node_name(child)
                if name:
                    self.symbols.append(SymbolInfo(
                        name=name,
                        kind="struct",
                        file_path=file_path,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        signature=f"struct {name}",
                    ))

    def _handle_go_const(self, node, file_path: str):
        for child in node.children:
            if child.type == "const_spec":
                name = self._get_node_name(child)
                if name:
                    self.symbols.append(SymbolInfo(
                        name=name,
                        kind="const",
                        file_path=file_path,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        signature=f"const {name}",
                    ))

    def _handle_go_assign(self, node, file_path: str, parent_name: str):
        for child in node.children:
            if child.type == "expression_list":
                for c in child.children:
                    if c.type == "identifier":
                        self.mutations.append(MutationInfo(
                            target=c.text.decode(),
                            source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                            line=node.start_point[0] + 1,
                            kind="assign",
                        ))

    def _extract_go_calls(self, node, caller_ref: str):
        for child in node.children:
            if child.type == self.GO_CALL:
                callee = self._get_node_name(child)
                if not callee:
                    for c in child.children:
                        if c.type == "selector_expression":
                            for cc in c.children:
                                if cc.type == "field_identifier":
                                    callee = cc.text.decode()
                                    break
                if callee:
                    self.calls.append(CallEdge(
                        caller=caller_ref,
                        callee=callee,
                        line=child.start_point[0] + 1,
                    ))
            self._extract_go_calls(child, caller_ref)

    def _build_go_signature(self, node) -> str:
        name = self._get_node_name(node)
        params = []
        ret = ""
        for child in node.children:
            if child.type == "parameter_list":
                for p in child.children:
                    if p.type == "parameter_declaration":
                        for c in p.children:
                            if c.type == "identifier":
                                params.append(c.text.decode())
            elif child.type == "type_identifier" and child.prev_sibling and child.prev_sibling.type == ")":
                ret = child.text.decode()
        sig = f"func {name}({', '.join(params)})"
        if ret:
            sig += f" {ret}"
        return sig

    # --- Rust handlers ---

    def _walk_rust(self, node, file_path: str, parent_name: str):
        new_parent = parent_name

        if node.type == self.RUST_FUNCTION:
            self._handle_rust_function(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.RUST_STRUCT:
            self._handle_rust_struct(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.RUST_CONST:
            self._handle_rust_const(node, file_path)
        elif node.type == self.RUST_ASSIGN:
            self._handle_rust_assign(node, file_path, parent_name)

        for child in node.children:
            self._walk_rust(child, file_path, new_parent)

    def _handle_rust_function(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        sig = self._build_rust_signature(node)
        self.symbols.append(SymbolInfo(
            name=name,
            kind="function",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
        ))
        caller_ref = f"{file_path}:{node.start_point[0] + 1}:{name}"
        self._extract_rust_calls(node, caller_ref)

    def _handle_rust_struct(self, node, file_path: str):
        name = self._get_node_name(node)
        if name:
            self.symbols.append(SymbolInfo(
                name=name,
                kind="struct",
                file_path=file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=f"struct {name}",
            ))

    def _handle_rust_const(self, node, file_path: str):
        name = self._get_node_name(node)
        if name:
            self.symbols.append(SymbolInfo(
                name=name,
                kind="const",
                file_path=file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=f"const {name}",
            ))

    def _handle_rust_assign(self, node, file_path: str, parent_name: str):
        for child in node.children:
            if child.type == "identifier":
                self.mutations.append(MutationInfo(
                    target=child.text.decode(),
                    source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                    line=node.start_point[0] + 1,
                    kind="assign",
                ))

    def _extract_rust_calls(self, node, caller_ref: str):
        for child in node.children:
            if child.type == self.RUST_CALL:
                callee = self._get_rust_call_name(child)
                if callee:
                    self.calls.append(CallEdge(
                        caller=caller_ref,
                        callee=callee,
                        line=child.start_point[0] + 1,
                    ))
            self._extract_rust_calls(child, caller_ref)

    def _build_rust_signature(self, node) -> str:
        name = self._get_node_name(node)
        params = []
        ret = ""
        for child in node.children:
            if child.type == "parameters":
                for p in child.children:
                    if p.type == "parameter":
                        for c in p.children:
                            if c.type == "identifier":
                                params.append(c.text.decode())
            elif child.type in ("type_identifier", "primitive_type") and child.prev_sibling and child.prev_sibling.type == "->":
                ret = child.text.decode()
        sig = f"fn {name}({', '.join(params)})"
        if ret:
            sig += f" -> {ret}"
        return sig

    # --- Java handlers ---

    def _walk_java(self, node, file_path: str, parent_name: str):
        new_parent = parent_name

        if node.type == self.JAVA_FUNCTION:
            self._handle_java_function(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.JAVA_CONSTRUCTOR:
            self._handle_java_constructor(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.JAVA_CLASS:
            self._handle_java_class(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.JAVA_VAR:
            self._handle_java_var(node, file_path, parent_name)

        for child in node.children:
            self._walk_java(child, file_path, new_parent)

    def _handle_java_function(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        sig = self._build_java_signature(node)
        self.symbols.append(SymbolInfo(
            name=name,
            kind="function",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
        ))
        caller_ref = f"{file_path}:{node.start_point[0] + 1}:{name}"
        self._extract_java_calls(node, caller_ref)

    def _handle_java_constructor(self, node, file_path: str):
        name = self._get_node_name(node)
        if not name:
            return

        self.symbols.append(SymbolInfo(
            name=name,
            kind="constructor",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"{name}()",
        ))

    def _handle_java_class(self, node, file_path: str):
        name = self._get_node_name(node)
        if name:
            self.symbols.append(SymbolInfo(
                name=name,
                kind="class",
                file_path=file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=f"class {name}",
            ))

    def _handle_java_var(self, node, file_path: str, parent_name: str):
        for child in node.children:
            if child.type == "variable_declarator":
                for c in child.children:
                    if c.type == "identifier":
                        self.mutations.append(MutationInfo(
                            target=c.text.decode(),
                            source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                            line=node.start_point[0] + 1,
                            kind="assign",
                        ))

    def _extract_java_calls(self, node, caller_ref: str):
        for child in node.children:
            if child.type == self.JAVA_CALL:
                callee = self._get_node_name(child)
                if callee:
                    self.calls.append(CallEdge(
                        caller=caller_ref,
                        callee=callee,
                        line=child.start_point[0] + 1,
                    ))
            self._extract_java_calls(child, caller_ref)

    def _build_java_signature(self, node) -> str:
        name = self._get_node_name(node)
        ret = ""
        params = []
        for child in node.children:
            if child.type == "formal_parameters":
                for p in child.children:
                    if p.type == "formal_parameter":
                        for c in p.children:
                            if c.type == "identifier":
                                params.append(c.text.decode())
            elif child.type in ("type_identifier", "void_type") and child.prev_sibling and child.prev_sibling.type in ("modifiers",):
                ret = child.text.decode()
        sig = f"{ret} {name}({', '.join(params)})"
        return sig

    # --- C handlers ---

    def _walk_c(self, node, file_path: str, parent_name: str):
        new_parent = parent_name

        if node.type == self.C_FUNCTION:
            self._handle_c_function(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.C_STRUCT:
            self._handle_c_struct(node, file_path)
            new_parent = self._get_node_name(node)
        elif node.type == self.C_VAR:
            self._handle_c_var(node, file_path, parent_name)

        for child in node.children:
            self._walk_c(child, file_path, new_parent)

    def _handle_c_function(self, node, file_path: str):
        name = None
        ret = ""
        for child in node.children:
            if child.type == "function_declarator":
                for c in child.children:
                    if c.type == "identifier":
                        name = c.text.decode()
            elif child.type in ("primitive_type", "type_identifier") and child.next_sibling and child.next_sibling.type in ("function_declarator", "pointer_declarator"):
                ret = child.text.decode()

        if not name:
            return

        sig = self._build_c_signature(node)
        self.symbols.append(SymbolInfo(
            name=name,
            kind="function",
            file_path=file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
        ))
        caller_ref = f"{file_path}:{node.start_point[0] + 1}:{name}"
        self._extract_c_calls(node, caller_ref)

    def _handle_c_struct(self, node, file_path: str):
        name = None
        for child in node.children:
            if child.type == "type_identifier":
                name = child.text.decode()
        if name:
            self.symbols.append(SymbolInfo(
                name=name,
                kind="struct",
                file_path=file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=f"struct {name}",
            ))

    def _handle_c_var(self, node, file_path: str, parent_name: str):
        for child in node.children:
            if child.type == "init_declarator":
                for c in child.children:
                    if c.type == "identifier":
                        self.mutations.append(MutationInfo(
                            target=c.text.decode(),
                            source=f"{file_path}:{node.start_point[0] + 1}:{parent_name}",
                            line=node.start_point[0] + 1,
                            kind="assign",
                        ))

    def _extract_c_calls(self, node, caller_ref: str):
        for child in node.children:
            if child.type == self.C_CALL:
                callee = self._get_node_name(child)
                if callee:
                    self.calls.append(CallEdge(
                        caller=caller_ref,
                        callee=callee,
                        line=child.start_point[0] + 1,
                    ))
            self._extract_c_calls(child, caller_ref)

    def _build_c_signature(self, node) -> str:
        name = None
        ret = ""
        params = []
        for child in node.children:
            if child.type == "function_declarator":
                for c in child.children:
                    if c.type == "identifier":
                        name = c.text.decode()
                    elif c.type == "parameter_list":
                        for p in c.children:
                            if p.type == "identifier":
                                params.append(p.text.decode())
            elif child.type in ("primitive_type", "type_identifier"):
                ret = child.text.decode()
        if not name:
            return ""
        sig = f"{ret} {name}({', '.join(params)})"
        return sig

    # --- Call extraction ---

    def _extract_python_calls(self, node, caller_ref: str):
        """Recursively find all call nodes."""
        for child in node.children:
            if child.type == self.PYTHON_CALL:
                callee = self._get_python_call_name(child)
                if callee:
                    self.calls.append(CallEdge(
                        caller=caller_ref,
                        callee=callee,
                        line=child.start_point[0] + 1,
                    ))
            self._extract_python_calls(child, caller_ref)

    def _extract_js_calls(self, node, caller_ref: str):
        """Recursively find all call_expression nodes."""
        for child in node.children:
            if child.type == self.TS_CALL:
                callee = self._get_js_call_name(child)
                if callee:
                    self.calls.append(CallEdge(
                        caller=caller_ref,
                        callee=callee,
                        line=child.start_point[0] + 1,
                    ))
            self._extract_js_calls(child, caller_ref)

    # --- Signature building ---

    def _build_python_signature(self, node) -> str:
        """Build a human-readable Python function signature."""
        name = self._get_node_name(node)
        params = []
        ret = ""

        for child in node.children:
            if child.type == "parameters":
                for p in child.children:
                    if p.type == "identifier":
                        if p.text.decode() == "self":
                            continue
                        params.append(p.text.decode())
                    elif p.type == "typed_parameter":
                        for c in p.children:
                            if c.type == "identifier":
                                if c.text.decode() == "self":
                                    break
                                params.append(c.text.decode())
            elif child.type == "type" and child.prev_sibling and child.prev_sibling.type == "->":
                ret = child.text.decode()

        sig = f"def {name}({', '.join(params)})"
        if ret:
            sig += f" -> {ret}"
        return sig

    def _build_js_signature(self, node) -> str:
        """Build a human-readable JS/TS function signature."""
        name = self._get_node_name(node)
        params = []
        ret = ""

        for child in node.children:
            if child.type == "formal_parameters":
                for p in child.children:
                    if p.type in ("identifier", "required_parameter", "identifier"): 
                        if p.type == "required_parameter":
                            for c in p.children:
                                if c.type == "identifier":
                                    params.append(c.text.decode())
                        elif p.type == "identifier":
                            params.append(p.text.decode())
            elif child.type == "type_annotation" and child.prev_sibling and child.prev_sibling.type == ":":
                ret = child.text.decode().strip(":")

        sig = f"function {name}({', '.join(params)})"
        if ret:
            sig += f": {ret}"
        return sig

    # --- Helpers ---

    @staticmethod
    def _get_node_name(node) -> Optional[str]:
        """Get the name identifier from a node."""
        for child in node.children:
            if child.type in ("identifier", "type_identifier", "property_identifier"):
                return child.text.decode()
        return None

    @staticmethod
    def _get_python_decorators(node) -> list[str]:
        """Extract decorator names from a Python function/class node."""
        decorators = []
        for child in node.children:
            if child.type == "decorator":
                for c in child.children:
                    if c.type == "identifier":
                        decorators.append(c.text.decode())
                    elif c.type == "call":
                        for cc in c.children:
                            if cc.type == "identifier":
                                decorators.append(cc.text.decode())
        return decorators

    @staticmethod
    def _get_python_call_name(node) -> Optional[str]:
        """Extract the name of a Python function call."""
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode()
            if child.type == "attribute":
                for c in child.children:
                    if c.type == "identifier" and c.prev_sibling and c.prev_sibling.type == ".":
                        return c.text.decode()
        return None

    @staticmethod
    def _get_js_call_name(node) -> Optional[str]:
        """Extract the name of a JS/TS function call."""
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode()
            if child.type == "member_expression":
                for c in child.children:
                    if c.type in ("property_identifier", "identifier") and c.prev_sibling and c.prev_sibling.type == ".":
                        return c.text.decode()
        return None

    @staticmethod
    def _get_python_assign_targets(node) -> list[str]:
        """Extract target names from a Python assignment."""
        targets = []
        for child in node.children:
            if child.type == "identifier":
                targets.append(child.text.decode())
            elif child.type == "attribute":
                name = ASTParser._python_target_name(child)
                if name:
                    targets.append(name)
            elif child.type == "subscript":
                val = ASTParser._python_target_name(child)
                if val:
                    targets.append(f"{val}[...]")
        return targets

    @staticmethod
    def _python_target_name(node) -> Optional[str]:
        """Extract name from a Python target node."""
        if node.type == "identifier":
            return node.text.decode()
        if node.type == "attribute":
            val = None
            for child in node.children:
                if child.type == "identifier" and child.prev_sibling and child.prev_sibling.type == ".":
                    return child.text.decode()
                if child.type in ("identifier", "attribute"):
                    val = ASTParser._python_target_name(child)
            return val
        return None

    @staticmethod
    def _get_python_aug_target(node) -> Optional[str]:
        """Extract target from a Python augmented assignment."""
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode()
            if child.type == "attribute":
                return ASTParser._python_target_name(child)
        return None

    @staticmethod
    def _get_var_declarator_name(node) -> Optional[str]:
        """Extract name from a variable_declarator."""
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode()
        return None

    @staticmethod
    def _get_rust_call_name(node) -> Optional[str]:
        """Extract the name of a Rust function call."""
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode()
            if child.type == "scoped_identifier":
                for c in child.children:
                    if c.type == "identifier":
                        return c.text.decode()
        return None

    @staticmethod
    def _get_js_assign_target(node) -> Optional[str]:
        """Extract target from a JS/TS assignment expression."""
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode()
            if child.type == "member_expression":
                for c in child.children:
                    if c.type in ("property_identifier", "identifier") and c.prev_sibling and c.prev_sibling.type == ".":
                        return c.text.decode()
        return None
