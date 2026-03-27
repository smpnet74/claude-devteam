"""Tests for knowledge admin CLI commands."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from devteam.cli.commands.knowledge_cmd import knowledge_app

runner = CliRunner()


def _make_mock_store():
    """Create a mock KnowledgeStore for CLI tests."""
    store = AsyncMock()
    store.is_connected = True

    # get_stats_detailed
    store.get_stats_detailed.return_value = {
        "total": 5,
        "verified": 2,
        "by_sharing": {"shared": 3, "project": 2},
        "by_project": {"myapp": 2},
    }

    # get_entry
    store.get_entry.return_value = {
        "id": "knowledge:abc",
        "content": "Test content",
        "summary": "Test summary",
        "verified": False,
    }

    # get_decay_candidates
    store.get_decay_candidates.return_value = []

    # list_all_entries for export
    store.list_all_entries.return_value = [
        {
            "id": "knowledge:abc",
            "content": "Test",
            "summary": "Test",
            "tags": ["process"],
            "sharing": "shared",
            "project": None,
            "embedding": [0.0] * 768,
            "verified": False,
        }
    ]

    return store


def _make_mock_embedder():
    """Create a mock OllamaEmbedder for CLI tests."""
    return MagicMock()


def _patch_ensure_connected(mock_store, mock_embedder=None):
    """Return a patch context that makes _ensure_connected return (store, embedder)."""
    if mock_embedder is None:
        mock_embedder = _make_mock_embedder()

    async def _fake_ensure():
        return mock_store, mock_embedder

    return patch(
        "devteam.cli.commands.knowledge_cmd._ensure_connected",
        side_effect=_fake_ensure,
    )


def _patch_ensure_connected_error(exc):
    """Return a patch context that makes _ensure_connected raise an exception."""
    async def _fake_ensure():
        raise exc

    return patch(
        "devteam.cli.commands.knowledge_cmd._ensure_connected",
        side_effect=_fake_ensure,
    )


class TestKnowledgeStats:
    def test_stats_displays_output(self):
        mock_store = _make_mock_store()
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["stats"])
            assert result.exit_code == 0
            assert "Knowledge Base Statistics" in result.output
            assert "Total entries:" in result.output
            assert "5" in result.output
            assert "Verified:" in result.output
            assert "2" in result.output

    def test_stats_shows_sharing_breakdown(self):
        mock_store = _make_mock_store()
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["stats"])
            assert "shared: 3" in result.output
            assert "project: 2" in result.output

    def test_stats_shows_project_breakdown(self):
        mock_store = _make_mock_store()
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["stats"])
            assert "myapp: 2" in result.output

    def test_stats_handles_store_unavailable(self):
        with _patch_ensure_connected_error(ConnectionError("DB down")):
            result = runner.invoke(knowledge_app, ["stats"])
            assert result.exit_code == 1


class TestKnowledgeVerify:
    def test_verify_entry(self):
        mock_store = _make_mock_store()
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["verify", "knowledge:abc"])
            assert result.exit_code == 0
            assert "verified" in result.output.lower()
            mock_store.update_entry.assert_awaited_once()

    def test_verify_handles_store_unavailable(self):
        with _patch_ensure_connected_error(ConnectionError("DB down")):
            result = runner.invoke(knowledge_app, ["verify", "knowledge:abc"])
            assert result.exit_code == 1


class TestKnowledgeRedact:
    def test_redact_entry(self):
        mock_store = _make_mock_store()
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["redact", "knowledge:abc"])
            assert result.exit_code == 0
            assert "Redacted" in result.output
            assert "Test summary" in result.output

    def test_redact_nonexistent_entry(self):
        mock_store = _make_mock_store()
        mock_store.get_entry.return_value = None
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["redact", "knowledge:missing"])
            assert result.exit_code == 1
            assert "not found" in result.output.lower()


class TestKnowledgePurge:
    def test_purge_by_id(self):
        mock_store = _make_mock_store()
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["purge", "knowledge:abc"])
            assert result.exit_code == 0
            assert "Purged entry" in result.output

    def test_purge_by_project(self):
        mock_store = _make_mock_store()
        mock_store.delete_by_project.return_value = 3
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["purge", "--project", "myapp"])
            assert result.exit_code == 0
            assert "3" in result.output

    def test_purge_older_than(self):
        mock_store = _make_mock_store()
        mock_store.get_decay_candidates.return_value = [
            {"id": "knowledge:old1"},
            {"id": "knowledge:old2"},
        ]
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["purge", "--older-than", "30"])
            assert result.exit_code == 0
            assert "2" in result.output
            assert "stale" in result.output.lower()

    def test_purge_older_than_no_matches(self):
        mock_store = _make_mock_store()
        mock_store.get_decay_candidates.return_value = []
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["purge", "--older-than", "30"])
            assert result.exit_code == 0
            assert "No entries" in result.output

    def test_purge_no_args_shows_error(self):
        mock_store = _make_mock_store()
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["purge"])
            assert result.exit_code == 1


class TestKnowledgeExport:
    def test_export_to_stdout(self):
        mock_store = _make_mock_store()
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["export"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["export_type"] == "knowledge"
            assert data["count"] >= 1

    def test_export_to_file(self, tmp_path):
        mock_store = _make_mock_store()
        output_file = str(tmp_path / "export.json")
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["export", "-o", output_file])
            assert result.exit_code == 0
            assert "Exported" in result.output
            with open(output_file) as f:
                data = json.load(f)
            assert data["export_type"] == "knowledge"

    def test_export_strips_embeddings(self):
        mock_store = _make_mock_store()
        with _patch_ensure_connected(mock_store):
            result = runner.invoke(knowledge_app, ["export"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            for entry in data["entries"]:
                assert "embedding" not in entry


class TestKnowledgeSearch:
    def test_search_delegates_to_query_tool(self):
        mock_store = _make_mock_store()
        mock_embedder = _make_mock_embedder()
        mock_tool = AsyncMock()
        mock_tool.query.return_value = "Knowledge results for: test\n\n### 1. Test"

        with (
            _patch_ensure_connected(mock_store, mock_embedder),
            patch(
                "devteam.knowledge.query_tool.QueryKnowledgeTool",
                return_value=mock_tool,
            ),
        ):
            result = runner.invoke(knowledge_app, ["search", "test query"])
            assert result.exit_code == 0
            assert "Knowledge results" in result.output

    def test_search_passes_limit(self):
        mock_store = _make_mock_store()
        mock_embedder = _make_mock_embedder()
        mock_tool = AsyncMock()
        mock_tool.query.return_value = "Knowledge results for: test\n\n### 1. Test"

        with (
            _patch_ensure_connected(mock_store, mock_embedder),
            patch(
                "devteam.knowledge.query_tool.QueryKnowledgeTool",
                return_value=mock_tool,
            ),
        ):
            result = runner.invoke(knowledge_app, ["search", "test query", "--limit", "10"])
            assert result.exit_code == 0
            mock_tool.query.assert_awaited_once_with("test query", scope="all", limit=10)

    def test_search_handles_store_unavailable(self):
        with _patch_ensure_connected_error(ConnectionError("DB down")):
            result = runner.invoke(knowledge_app, ["search", "test query"])
            assert result.exit_code == 1
