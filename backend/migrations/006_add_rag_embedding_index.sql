-- Migration 006: Add IVFFlat indexes for vector similarity search
-- This significantly speeds up semantic search for cell crops and images
--
-- NOTE: RAG document embeddings (2048-dim Qwen VL) CANNOT be indexed because
-- pgvector has a 2000 dimension limit for IVFFlat/HNSW indexes.
-- For RAG, we use exact search (which is still fast for small datasets).

-- IVFFlat index for cell crop embeddings (1024-dim DINOv2)
-- Used for finding similar cells across experiments
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cell_crops_embedding
ON cell_crops USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- IVFFlat index for FOV image embeddings (1024-dim DINOv2)
-- Used for finding similar microscopy images
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_images_embedding
ON images USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- IVFFlat index for protein embeddings (1152-dim ESM-C)
-- Used for finding proteins with similar sequences
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_map_proteins_embedding
ON map_proteins USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 50);

-- Comments for documentation
COMMENT ON INDEX idx_cell_crops_embedding IS 'IVFFlat index for fast ANN search on cell crop DINOv2 embeddings';
COMMENT ON INDEX idx_images_embedding IS 'IVFFlat index for fast ANN search on FOV image embeddings';
COMMENT ON INDEX idx_map_proteins_embedding IS 'IVFFlat index for fast ANN search on protein ESM-C embeddings';
