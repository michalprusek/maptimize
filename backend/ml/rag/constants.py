"""Lightweight RAG constants (no ML dependencies).

Kept separate from ``qwen_vl_encoder`` so that importers which only need the
embedding dimension (e.g. SQLAlchemy models) do not pull in torch/transformers.
"""

QWEN_VL_EMBEDDING_DIM = 2048  # Qwen3-VL-Embedding-2B output dimension
