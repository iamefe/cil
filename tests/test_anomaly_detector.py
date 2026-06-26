import tempfile
import os

from cil.indexer.anomaly_detector import AnomalyDetector


class TestAnomalyDetector:
    def setup_method(self):
        self.detector = AnomalyDetector()

    def _write_file(self, source):
        fd, path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write(source)
        return path

    def _analyze(self, source):
        path = self._write_file(source)
        try:
            tree = __import__("ast").parse(source, filename=path)
            symbols = []
            imports = []
            anomalies = self.detector.analyze_file(path, symbols, imports)
            return anomalies
        finally:
            os.unlink(path)

    def test_bare_except(self):
        source = '''
try:
    pass
except:
    pass
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "bare_except" for a in anomalies)

    def test_no_bare_except(self):
        source = '''
try:
    pass
except ValueError:
    pass
'''
        anomalies = self._analyze(source)
        assert not any(a["type"] == "bare_except" for a in anomalies)

    def test_bare_raise(self):
        source = '''
try:
    pass
except:
    raise
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "bare_raise" for a in anomalies)

    def test_mutable_default(self):
        source = '''
def foo(x=[]):
    pass
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "mutable_default" for a in anomalies)

    def test_no_mutable_default(self):
        source = '''
def foo(x=1):
    pass
'''
        anomalies = self._analyze(source)
        assert not any(a["type"] == "mutable_default" for a in anomalies)

    def test_long_function(self):
        source = "def foo():\n" + "    x = 1\n" * 85
        anomalies = self._analyze(source)
        assert any(a["type"] == "long_function" for a in anomalies)

    def test_short_function(self):
        source = "def foo():\n    pass\n"
        anomalies = self._analyze(source)
        assert not any(a["type"] == "long_function" for a in anomalies)

    def test_deep_nesting(self):
        source = '''
if True:
    if True:
        if True:
            if True:
                if True:
                    pass
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "deep_nesting" for a in anomalies)

    def test_resource_leak(self):
        source = '''
f = open("file.txt")
data = f.read()
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "resource_leak" for a in anomalies)

    def test_no_resource_leak(self):
        source = '''
with open("file.txt") as f:
    data = f.read()
'''
        anomalies = self._analyze(source)
        assert not any(a["type"] == "resource_leak" for a in anomalies)

    def test_global_mutation(self):
        source = '''
def foo():
    global x
    x = 1
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "global_mutation" for a in anomalies)

    def test_missing_init(self):
        source = '''
class Foo:
    def bar(self):
        pass
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "missing_init" for a in anomalies)

    def test_has_init(self):
        source = '''
class Foo:
    def __init__(self):
        pass
    def bar(self):
        pass
'''
        anomalies = self._analyze(source)
        assert not any(a["type"] == "missing_init" for a in anomalies)

    def test_star_import(self):
        source = '''
from os import *
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "star_import" for a in anomalies)

    def test_eval_exec(self):
        source = '''
eval("1+1")
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "eval_exec" for a in anomalies)

    def test_hardcoded_secret(self):
        source = '''
password = "secret123"
'''
        anomalies = self._analyze(source)
        assert any(a["type"] == "hardcoded_secret" for a in anomalies)

    def test_clean_file(self):
        source = '''
def hello(name):
    return f"hello {name}"
'''
        anomalies = self._analyze(source)
        assert len(anomalies) == 0

    def test_unused_import(self):
        source = '''
import os

def foo():
    pass
'''
        path = self._write_file(source)
        try:
            anomalies = self.detector.analyze_file(path, [], ["os"])
            assert any(a["type"] == "unused_import" for a in anomalies)
        finally:
            os.unlink(path)

    def test_severity_levels(self):
        source = '''
try:
    pass
except:
    raise

def foo(x=[]):
    pass

eval("1+1")
'''
        anomalies = self._analyze(source)
        severities = {a["severity"] for a in anomalies}
        assert "high" in severities
        assert "medium" in severities
