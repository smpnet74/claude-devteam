"""Tests for Ollama embedding integration."""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from devteam.knowledge.embeddings import OllamaEmbedder, EmbeddingError


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response with sync json() method."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


@pytest.fixture
def mock_ollama_response():
    """A mock Ollama /api/embed response."""
    return {
        "model": "nomic-embed-text",
        "embeddings": [[0.1] * 768],
    }


@pytest.fixture
def mock_ollama_batch_response():
    """A mock batch Ollama /api/embed response."""
    return {
        "model": "nomic-embed-text",
        "embeddings": [[0.1] * 768, [0.2] * 768, [0.3] * 768],
    }


class TestOllamaEmbedder:
    @pytest.mark.asyncio
    async def test_embed_single_text(self, mock_ollama_response):
        embedder = OllamaEmbedder()
        mock_resp = _mock_response(mock_ollama_response)

        with patch.object(embedder._client, "post", AsyncMock(return_value=mock_resp)):
            result = await embedder.embed("test text")
            assert len(result) == 768
            assert all(isinstance(v, float) for v in result)

    @pytest.mark.asyncio
    async def test_embed_batch(self, mock_ollama_batch_response):
        embedder = OllamaEmbedder()
        mock_resp = _mock_response(mock_ollama_batch_response)

        with patch.object(embedder._client, "post", AsyncMock(return_value=mock_resp)):
            results = await embedder.embed_batch(["text1", "text2", "text3"])
            assert len(results) == 3
            assert all(len(v) == 768 for v in results)

    @pytest.mark.asyncio
    async def test_embed_uses_correct_model(self, mock_ollama_response):
        embedder = OllamaEmbedder(model="nomic-embed-text")
        mock_resp = _mock_response(mock_ollama_response)
        mock_post = AsyncMock(return_value=mock_resp)

        with patch.object(embedder._client, "post", mock_post):
            await embedder.embed("test")
            call_args = mock_post.call_args
            assert call_args[1]["json"]["model"] == "nomic-embed-text"

    @pytest.mark.asyncio
    async def test_embed_connection_error_raises_embedding_error(self):
        embedder = OllamaEmbedder()
        with patch.object(
            embedder._client, "post", AsyncMock(side_effect=httpx.ConnectError("refused"))
        ):
            with pytest.raises(EmbeddingError, match="Ollama unavailable"):
                await embedder.embed("test")

    @pytest.mark.asyncio
    async def test_embed_http_error_raises_embedding_error(self):
        embedder = OllamaEmbedder()
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_resp
        )

        with patch.object(embedder._client, "post", AsyncMock(return_value=mock_resp)):
            with pytest.raises(EmbeddingError, match="Ollama embedding failed"):
                await embedder.embed("test")

    @pytest.mark.asyncio
    async def test_custom_base_url(self):
        embedder = OllamaEmbedder(base_url="http://remote:11434")
        assert embedder.base_url == "http://remote:11434"

    @pytest.mark.asyncio
    async def test_is_available_true(self, mock_ollama_response):
        embedder = OllamaEmbedder()
        mock_resp = _mock_response(mock_ollama_response)

        with patch.object(embedder._client, "post", AsyncMock(return_value=mock_resp)):
            assert await embedder.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_false_on_connection_error(self):
        embedder = OllamaEmbedder()
        with patch.object(
            embedder._client, "post", AsyncMock(side_effect=httpx.ConnectError("refused"))
        ):
            assert await embedder.is_available() is False

    @pytest.mark.asyncio
    async def test_default_model(self):
        embedder = OllamaEmbedder()
        assert embedder.model == "nomic-embed-text"

    @pytest.mark.asyncio
    async def test_default_base_url(self):
        embedder = OllamaEmbedder()
        assert embedder.base_url == "http://localhost:11434"
