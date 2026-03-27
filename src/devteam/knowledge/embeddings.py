"""Ollama embedding integration for knowledge vector search."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "nomic-embed-text"
EMBEDDING_DIMENSIONS = 768


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""


class OllamaEmbedder:
    """Generates text embeddings via Ollama's local API.

    Uses nomic-embed-text (768 dimensions) by default.
    No external API dependency -- runs entirely locally.
    """

    def __init__(
        self,
        base_url: str = OLLAMA_DEFAULT_BASE_URL,
        model: str = OLLAMA_DEFAULT_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
        )

    async def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text.

        Args:
            text: The text to embed.

        Returns:
            A list of floats (768 dimensions for nomic-embed-text).

        Raises:
            EmbeddingError: If Ollama is unavailable or returns an error.
        """
        result = await self._call_ollama([text])
        return result[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for multiple texts in one call.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors, one per input text.

        Raises:
            EmbeddingError: If Ollama is unavailable or returns an error.
        """
        return await self._call_ollama(texts)

    async def is_available(self) -> bool:
        """Check if Ollama is running and the model is available."""
        try:
            await self._call_ollama(["test"])
            return True
        except EmbeddingError:
            return False

    async def _call_ollama(self, texts: list[str]) -> list[list[float]]:
        """Make the actual HTTP call to Ollama's /api/embed endpoint."""
        try:
            response = await self._client.post(
                "/api/embed",
                json={
                    "model": self.model,
                    "input": texts,
                },
            )
            response.raise_for_status()
        except httpx.ConnectError as e:
            raise EmbeddingError(f"Ollama unavailable at {self.base_url}: {e}") from e
        except httpx.TimeoutException as e:
            raise EmbeddingError(f"Ollama timed out at {self.base_url}: {e}") from e
        except httpx.HTTPStatusError as e:
            raise EmbeddingError(
                f"Ollama embedding failed (HTTP {e.response.status_code}): {e}"
            ) from e

        data = response.json()
        return [list(map(float, emb)) for emb in data["embeddings"]]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
