SECRET_KEYWORDS = {"password", "secret", "key", "token", "api_key", "apikey", "api_secret"}

def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)

def check_long_functions(root_node, file_path: str, anomalies: list[dict], func_types: list[str]):
    for node in _walk(root_node):
        if node.type not in func_types:
            continue
        end_line = node.end_point[0] + 1
        start_line = node.start_point[0] + 1
        length = end_line - start_line
        if length > 80:
            name = _get_func_name(node)
            anomalies.append({
                "type": "long_function",
                "severity": "medium",
                "file_path": file_path,
                "line": start_line,
                "message": f"Function {name}() is {length} lines — consider splitting",
            })

def check_deep_nesting(root_node, file_path: str, anomalies: list[dict], nesting_types: tuple[str, ...]):
    def _check_depth(node, depth):
        if node.type in nesting_types:
            depth += 1
            if depth > 4:
                anomalies.append({
                    "type": "deep_nesting",
                    "severity": "medium",
                    "file_path": file_path,
                    "line": node.start_point[0] + 1,
                    "message": f"Nesting depth {depth} — consider extracting to a function",
                })
        for child in node.children:
            _check_depth(child, depth)
    _check_depth(root_node, 0)

def check_hardcoded_secrets(root_node, file_path: str, anomalies: list[dict], assign_type: str, target_fn=None, value_is_string_literal_fn=None):
    for node in _walk(root_node):
        if node.type != assign_type:
            continue
        name = None
        if target_fn:
            name = target_fn(node)
        else:
            for child in node.children:
                if child.type in ("identifier", "type_identifier"):
                    name = child.text.decode()
                    break
        if not name:
            continue
        lower_name = name.lower()
        if not any(kw in lower_name for kw in SECRET_KEYWORDS):
            continue
        is_string = False
        if value_is_string_literal_fn:
            is_string = value_is_string_literal_fn(node)
        else:
            for child in _walk(node):
                if child.type in ("string", "string_literal", "interpreted_string_literal", "character_literal"):
                    is_string = True
                    break
        if is_string:
            anomalies.append({
                "type": "hardcoded_secret",
                "severity": "high",
                "file_path": file_path,
                "line": node.start_point[0] + 1,
                "message": f"Potential hardcoded secret in variable '{name}'",
            })

def _get_func_name(node) -> str:
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "property_identifier"):
            return child.text.decode()
        if child.type == "function_declarator":
            for c in child.children:
                if c.type == "identifier":
                    return c.text.decode()
    return "<anonymous>"