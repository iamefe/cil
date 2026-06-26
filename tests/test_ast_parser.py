import ast
import tempfile
import os

from cil.indexer.ast_parser import ASTParser
from cil.models import FileIndex


class TestASTParser:
    def setup_method(self):
        self.parser = ASTParser()

    def _write_file(self, source):
        fd, path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write(source)
        return path

    def test_parse_functions(self):
        source = '''
def hello(name: str) -> str:
    """Say hello."""
    return f"hello {name}"

async def fetch(url):
    pass
'''
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert len(fi.symbols) == 2
            assert fi.symbols[0].name == "hello"
            assert fi.symbols[0].kind == "function"
            assert fi.symbols[0].docstring == "Say hello."
            assert "name" in fi.symbols[0].signature
            assert fi.symbols[1].name == "fetch"
        finally:
            os.unlink(path)

    def test_parse_class(self):
        source = '''
class Greeter:
    """A greeter class."""
    def greet(self, name):
        pass
'''
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert len(fi.symbols) == 2
            assert fi.symbols[0].name == "Greeter"
            assert fi.symbols[0].kind == "class"
            assert fi.symbols[0].docstring == "A greeter class."
            assert fi.symbols[1].name == "greet"
        finally:
            os.unlink(path)

    def test_parse_decorators(self):
        source = '''
@staticmethod
def add(a, b):
    return a + b

@property
def name(self):
    return self._name
'''
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert fi.symbols[0].decorators == ["staticmethod"]
            assert fi.symbols[1].decorators == ["property"]
        finally:
            os.unlink(path)

    def test_parse_imports(self):
        source = '''
import os
import sys as system
from pathlib import Path
from typing import List, Dict
'''
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert "os" in fi.imports
            assert "sys as system" in fi.imports
            assert "from pathlib import Path" in fi.imports
            assert "from typing import List" in fi.imports
        finally:
            os.unlink(path)

    def test_parse_calls(self):
        source = '''
def main():
    result = hello("world")
    os.path.join("a", "b")
'''
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert any("hello" in c.callee for c in self.parser.calls)
            assert any("join" in c.callee for c in self.parser.calls)
        finally:
            os.unlink(path)

    def test_parse_mutations(self):
        source = '''
x = 1
y += 1
del z
'''
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert len(self.parser.mutations) == 3
            assert self.parser.mutations[0].kind == "assign"
            assert self.parser.mutations[1].kind == "augment"
            assert self.parser.mutations[2].kind == "delete"
        finally:
            os.unlink(path)

    def test_syntax_error(self, capsys):
        source = "def foo("
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert isinstance(fi, FileIndex)
            assert len(fi.symbols) == 0
            captured = capsys.readouterr()
            assert "syntax error" in captured.out.lower()
        finally:
            os.unlink(path)

    def test_top_level_imports_only(self):
        source = '''
import os

def foo():
    import sys
'''
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert "os" in fi.imports
            assert not any("sys" in i for i in fi.imports)
        finally:
            os.unlink(path)

    def test_signature_with_return(self):
        source = '''
def add(a: int, b: int) -> int:
    return a + b
'''
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert "int" in fi.symbols[0].signature
        finally:
            os.unlink(path)

    def test_signature_no_return(self):
        source = '''
def greet(name):
    print(name)
'''
        path = self._write_file(source)
        try:
            fi = self.parser.parse_file(path)
            assert "name" in fi.symbols[0].signature
            assert "->" not in fi.symbols[0].signature
        finally:
            os.unlink(path)
