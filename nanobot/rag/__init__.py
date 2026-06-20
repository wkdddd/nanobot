"""RAG subsystem - retrieval-augmented generation with FTS5 + vector search + rerank."""

from nanobot.rag.chunker import TreeSitterChunker
from nanobot.rag.config import (
    EmbeddingConfig,
    QdrantConfig,
    RAGConfig,
    RAGRetrievalConfig,
    RerankConfig,
)
from nanobot.rag.embedding import EmbeddingClient, create_embedding_client_from_config
from nanobot.rag.index import RAGIndex
from nanobot.rag.qdrant_store import QdrantVectorHit, QdrantVectorStore
from nanobot.rag.rerank import RerankClient, create_rerank_client_from_config
from nanobot.rag.review import rrf_merge
from nanobot.rag.runtime import RAGRuntime, create_rag_runtime
from nanobot.rag.utils import (
    ChunkerFn,
    ChunkKey,
    IndexedChunk,
    IndexedHit,
    best_snippet,
    chunk_key,
    hit_key,
    query_terms,
)

__all__ = [
    "ChunkKey",
    "ChunkerFn",
    "EmbeddingClient",
    "EmbeddingConfig",
    "IndexedChunk",
    "IndexedHit",
    "QdrantConfig",
    "QdrantVectorHit",
    "QdrantVectorStore",
    "RAGConfig",
    "RAGIndex",
    "RAGRetrievalConfig",
    "RAGRuntime",
    "RerankClient",
    "RerankConfig",
    "TreeSitterChunker",
    "best_snippet",
    "chunk_key",
    "create_embedding_client_from_config",
    "create_rag_runtime",
    "create_rerank_client_from_config",
    "hit_key",
    "query_terms",
    "rrf_merge",
]
