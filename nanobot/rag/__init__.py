"""RAG subsystem — retrieval-augmented generation with FTS5 + hnswlib + rerank."""

from nanobot.rag.chunker import TreeSitterChunker
from nanobot.rag.embedding import EmbeddingClient, create_embedding_client_from_config
from nanobot.rag.index import RAGIndex
from nanobot.rag.qdrant_store import QdrantMathVectorStore, QdrantVectorHit
from nanobot.rag.rerank import RerankClient, create_rerank_client_from_config
from nanobot.rag.utils import (
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
    "QdrantMathVectorStore",
    "QdrantVectorHit",
    "RAGIndex",
    "RerankClient",
    "TreeSitterChunker",
    "best_snippet",
    "create_embedding_client_from_config",
    "create_rerank_client_from_config",
    "query_terms",
]
