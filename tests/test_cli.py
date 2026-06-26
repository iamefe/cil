import sys
from unittest.mock import MagicMock, patch

from cil.cli import main


class TestCLI:
    def _mock_collection(self):
        col = MagicMock()
        col.find.return_value = []
        col.replace_one.return_value = MagicMock()
        col.delete_one.return_value = MagicMock()
        col.create_index.return_value = "idx"
        return col

    def test_index_command(self, tmp_path, capsys):
        project = tmp_path / "test_project"
        project.mkdir()
        (project / "main.py").write_text('def hello():\n    pass\n')

        mock_col = self._mock_collection()

        with patch("cil.cli.get_collection", return_value=mock_col):
            with patch("cil.cli.ensure_indexes"):
                sys.argv = ["cil", "index", str(project)]
                main()

        captured = capsys.readouterr()
        assert "Indexed" in captured.out
        assert "Files:" in captured.out
        mock_col.replace_one.assert_called_once()

    def test_index_force(self, tmp_path, capsys):
        project = tmp_path / "test_project"
        project.mkdir()
        (project / "main.py").write_text('def hello():\n    pass\n')

        mock_col = self._mock_collection()

        with patch("cil.cli.get_collection", return_value=mock_col):
            with patch("cil.cli.ensure_indexes"):
                sys.argv = ["cil", "index", str(project), "--force"]
                main()

        mock_col.delete_one.assert_called_once()

    def test_status_no_projects(self, capsys):
        mock_col = self._mock_collection()
        mock_col.find.return_value = []

        with patch("cil.cli.get_collection", return_value=mock_col):
            with patch("cil.cli.ensure_indexes"):
                sys.argv = ["cil", "status"]
                main()

        captured = capsys.readouterr()
        assert "No indexed projects" in captured.out

    def test_query_no_match(self, capsys):
        mock_col = self._mock_collection()
        mock_col.find.return_value = []

        with patch("cil.cli.get_collection", return_value=mock_col):
            with patch("cil.cli.ensure_indexes"):
                sys.argv = ["cil", "query", "nonexistent"]
                main()

        captured = capsys.readouterr()
        assert "No symbols matching" in captured.out

    def test_anomalies_no_results(self, capsys):
        mock_col = self._mock_collection()
        mock_col.find.return_value = []

        with patch("cil.cli.get_collection", return_value=mock_col):
            with patch("cil.cli.ensure_indexes"):
                sys.argv = ["cil", "anomalies"]
                main()

        captured = capsys.readouterr()
        assert "No anomalies found" in captured.out

    def test_anomalies_with_severity_filter(self, capsys):
        mock_col = self._mock_collection()
        mock_col.find.return_value = [{
            "anomalies": [
                {"severity": "high", "file_path": "a.py", "line": 1, "type": "bare_except", "message": "test"},
                {"severity": "low", "file_path": "b.py", "line": 2, "type": "unused_import", "message": "test2"},
            ]
        }]

        with patch("cil.cli.get_collection", return_value=mock_col):
            with patch("cil.cli.ensure_indexes"):
                sys.argv = ["cil", "anomalies", "--severity", "high"]
                main()

        captured = capsys.readouterr()
        assert "[HIGH]" in captured.out
        assert "[LOW]" not in captured.out
