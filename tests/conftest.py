import sys
import os

# Add src to path for development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import MagicMock


def mock_collection():
    """Return a mock MongoDB collection with all required methods."""
    col = MagicMock()
    col.find.return_value = []
    col.replace_one.return_value = MagicMock()
    col.delete_one.return_value = MagicMock()
    col.create_index.return_value = "idx"
    col.count_documents.return_value = 0
    return col


def mock_db():
    """Return a mock MongoDB database."""
    db = MagicMock()
    db.command.return_value = {"ok": 1.0}
    db.__getitem__ = lambda self, name: mock_collection()
    return db


def tmp_project(tmp_path):
    """Create a temp directory with sample Python files."""
    project = tmp_path / "test_project"
    project.mkdir()

    (project / "main.py").write_text(
        'def hello():\n    print("hello")\n\nclass Greeter:\n    def greet(self):\n        pass\n'
    )

    (project / "utils.py").write_text(
        'import os\n\ndef helper():\n    pass\n'
    )

    return project
