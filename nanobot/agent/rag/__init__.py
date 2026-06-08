"""RAG subsystem — retrieval-augmented generation with FTS5 + hnswlib + rerank."""

from nanobot.agent.rag.chunker import TreeSitterChunker
from nanobot.agent.rag.embedding import EmbeddingClient, create_embedding_client_from_config
from nanobot.agent.rag.index import RAGIndex
from nanobot.agent.rag.rerank import RerankClient, create_rerank_client_from_config
from nanobot.agent.rag.utils import (
    ChunkerFn,
    ChunkKey,
    IndexedChunk,
    IndexedHit,
    best_snippet,
    query_terms,
)

__all__ = [
    "ChunkKey",
    "ChunkerFn",
    "EmbeddingClient",
    "IndexedChunk",
    "IndexedHit",
    "RAGIndex",
    "RerankClient",
    "TreeSitterChunker",
    "best_snippet",
    "create_embedding_client_from_config",
    "create_rerank_client_from_config",
    "query_terms",
]
