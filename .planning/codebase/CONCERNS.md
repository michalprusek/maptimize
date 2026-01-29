# Codebase Concerns

**Analysis Date:** 2026-01-29

## Tech Debt

### Monolithic Service Files

**Area: Giant service files creating complexity and testing challenges**

- Issue: `backend/services/gemini_agent_service.py` is 2085 lines - contains tool definitions, execution logic, RAG integration, Python code execution, and database queries all in one file
- Files: `backend/services/gemini_agent_service.py`, `backend/routers/images.py` (1374 lines), `backend/services/import_service.py` (1031 lines)
- Impact: Difficult to test individual components, high cognitive load for developers, risk of unintended side effects when modifying
- Fix approach: Break into smaller modules: separate tools execution, RAG handlers, database operations, and external API calls into dedicated files

### Incomplete Type Annotations

**Area: Many functions lack proper type hints**

- Issue: Frontend API client has untyped or partially typed requests
- Files: `frontend/lib/api.ts` - many endpoints return generic types without full type safety
- Impact: Risk of runtime errors, difficult refactoring, poor IDE support
- Fix approach: Add complete type definitions for all API responses

### Duplicate Hard-coded Credentials

**Area: Default passwords in seed data**

- Issue: Default admin password "82c17878" is hardcoded in version control
- Files: `backend/database.py` line 209
- Impact: Production security risk if database is reset
- Fix approach: Use environment variable for default password, or require password change on first login

### Missing RAG Vector Index Due to Dimension Limits

**Area: Vector database scaling limitation**

- Issue: RAG embeddings use 2048 dimensions (Qwen VL) which exceeds pgvector's 2000-dimension limit for HNSW/ivfflat indexes. Exact search is used instead (no index)
- Files: `backend/database.py` lines 178-183, `backend/services/rag_service.py`
- Impact: Slow semantic search on large document collections (O(n) instead of O(log n)), poor scaling for 1000+ documents
- Fix approach: Reduce embedding dimensions via PCA, migrate to dedicated vector DB (Qdrant/Pinecone), or implement approximate search without index

---

## Known Bugs

### Download Token Security Vulnerability

**Bug: Token exposed in URL for file downloads**

- Symptoms: Tokens appear in plaintext in download URLs and browser history
- Files: `frontend/lib/api.ts` line 895 (TODO comment acknowledges this)
- Trigger: Any file download operation exposes auth token in referrer header
- Workaround: Use short-lived tokens (currently not implemented)
- Fix: Implement single-use download tokens with automatic expiration

### CASCADE DELETE Data Loss Risk

**Bug: Deleting cell crops cascades to comparison history**

- Symptoms: Deleting a cell crop removes associated ranking comparisons permanently
- Files: `backend/models/ranking.py` lines 28, 32, 75-77 (CASCADE deletes defined)
- Trigger: DELETE /api/images/crops/{id} without confirm parameter
- Current mitigation: API requires `?confirm_delete_comparisons=true` to proceed (documented in CLAUDE.md)
- Fix: Soft-delete pattern or orphan comparison records instead of cascade

---

## Security Considerations

### Python Code Execution Sandbox Limitations

**Risk: RestrictedPython sandbox could be bypassed**

- Issue: Code execution service uses RestrictedPython for sandboxing user-provided Python code
- Files: `backend/services/code_execution_service.py` lines 1-50
- Current mitigation: Whitelist imports, block dunder attributes, check forbidden AST nodes, resource limits (16GB memory, 60s timeout)
- Gaps: Complex escape vectors possible through library internals, no kernel-level isolation
- Recommendations:
  - Run code in separate process/container for isolation
  - Monitor and log all executions
  - Add code complexity analysis (AST depth limits)
  - Consider switching to safer language (Lua/Wasm)

### Unauthenticated Vector Search Potential

**Risk: RAG document search may leak information**

- Issue: `backend/services/rag_service.py` search_documents function
- Files: `backend/services/rag_service.py`
- Current mitigation: User ID filtering on all queries
- Gap: If user IDs leaked, attackers could enumerate other users' documents
- Recommendations: Implement rate limiting on search, audit search patterns, consider field-level encryption for sensitive documents

### Admin Password Reset Token Vulnerability

**Risk: Admin password reset tokens lack expiration**

- Issue: Password reset functionality in `backend/routers/admin.py` generates new passwords but no token-based reset flow
- Files: `backend/routers/admin.py` reset_user_password endpoint
- Current mitigation: Admin-only access, logs all resets
- Recommendation: Implement time-limited password reset tokens instead of direct admin-set passwords

### File Upload Path Traversal Potential

**Risk: File upload could write outside intended directory**

- Issue: Despite UUID filename generation, symlink attacks possible
- Files: `backend/routers/images.py` lines 238-245
- Current mitigation: UUID-based filenames, path validation
- Recommendation: Validate all user input paths, use chroot/chown for uploaded files, implement strict path normalization

---

## Performance Bottlenecks

### Large Image Processing Memory Spikes

**Problem: Processing Z-stacks can consume excessive GPU/CPU memory**

- Issue: Z-stack projection creates full resolution arrays before downsampling
- Files: `backend/services/image_processor.py` lines 100-140
- Cause: Loading entire Z-stack into memory (sometimes 100+ slices × 2048×2048 px)
- Impact: Crashes on large stacks, timeout on slow systems
- Improvement path: Stream-process slices, implement memory-aware chunking

### Vector Search Performance (No Index)

**Problem: RAG semantic search is O(n) across all documents**

- Issue: 2048-dim embeddings exceed pgvector index limits
- Files: `backend/services/rag_service.py` similarity search
- Cause: Fall back to exact search (full table scan)
- Impact: Search time: 5-10s per user with 100+ documents
- Improvement path: See "Missing RAG Vector Index" under Tech Debt

### Agent Tool Execution Serialization

**Problem: Slow JSON serialization of large datasets**

- Issue: `_serialize_for_json()` recursively processes large numpy arrays and DataFrames
- Files: `backend/services/gemini_agent_service.py` lines 24-53
- Cause: Recursive type checking on every object in nested structures
- Impact: Multi-second delays for 100K+ row results
- Improvement path: Use fast serializers (orjson), implement streaming for large results

### SQL Query Validation Overhead

**Problem: Complex regex and string parsing for every agent query**

- Issue: `query_database` tool manually validates SQL instead of using parameterized queries
- Files: `backend/services/gemini_agent_service.py` lines 1356-1410
- Cause: Multiple passes over query string (keyword checks, regex, parsing)
- Impact: ~50-100ms overhead per query
- Improvement path: Use SQLAlchemy ORM for queries (eliminates SQL injection risk and validation overhead)

---

## Fragile Areas

### Image Editor State Management

**Component: `frontend/components/editor/ImageEditorPage.tsx`**

- Files: `frontend/components/editor/ImageEditorPage.tsx` (1760 lines), supporting hooks and utilities
- Why fragile: Giant component with complex canvas state, multiple undo/redo systems, mask manipulation, segmentation overlay coordination
- Safe modification:
  - Extract canvas rendering logic to separate component
  - Move state hooks to custom hook (useImageEditorState)
  - Separate concerns: canvas rendering, bbox interaction, mask editing, segmentation
- Test coverage gaps: No unit tests for canvas coordinate transformations, zoom/pan interactions, undo/redo consistency

### Async Task Tracking in Chat

**Component: `backend/routers/chat.py` streaming generator**

- Files: `backend/routers/chat.py` lines with `_active_tasks` dict
- Why fragile: Manual task tracking with dictionary, potential race conditions on concurrent requests, no cleanup guarantee
- Safe modification: Use asyncio.TaskGroup (Python 3.11+) or explicit finally blocks for cleanup
- Test coverage gaps: No tests for interrupted streams, concurrent request cleanup

### Model Factory Lazy Loading

**Component: `backend/ml/segmentation/sam_factory.py` and feature extractors**

- Files: `backend/ml/detection/detector.py` lines 62-80, encoder files
- Why fragile: Lazy model loading on first use causes unpredictable initialization delays and error timing
- Safe modification:
  - Eager load models at startup
  - Implement health check endpoint for model availability
  - Add model warm-up on server start
- Test coverage gaps: No tests for missing model weights, device fallback behavior

---

## Scaling Limits

### Database Connection Pool Exhaustion

**Resource: PostgreSQL connections**

- Current capacity: Default asyncpg pool = 10 connections
- Limit: Concurrent requests exceed 10 → queuing/timeouts
- Files: `backend/database.py` line 18-28 (async_sessionmaker config)
- Scaling path: Increase pool size (careful with production DB), implement connection pooling proxy (PgBouncer), use read replicas

### GPU Memory Saturation

**Resource: GPU VRAM (RTX A5000 16GB allocated to Maptimize)**

- Current capacity: 16GB
- Limit: Large Z-stacks + segmentation models + feature extraction exhaust VRAM
- Files: Multiple ML modules
- Scaling path: Implement model sharing across requests, quantize models (INT8), use smaller variant selection based on input size

### Vector Embedding Storage

**Resource: pgvector table storage**

- Current capacity: Embedded in PostgreSQL (no separate vector DB)
- Limit: At scale (100K+ embeddings), sequential scan becomes prohibitive
- Files: `backend/database.py` line 139 (RAG embedding column)
- Scaling path: Migrate to Qdrant/Pinecone at 50K+ documents, implement embedding pruning/archival

### Chat Message History

**Resource: Unbounded memory accumulation in AgentMemory**

- Current capacity: All memories stored in DB, loaded on every chat request
- Limit: Large memory stores (1000+ entries) slow down response generation
- Files: `backend/services/gemini_agent_service.py` memory operations
- Scaling path: Implement memory summarization, archive old entries, implement semantic deduplication

---

## Dependencies at Risk

### RestrictedPython Maintenance Risk

**Package: RestrictedPython (untrusted user code execution)**

- Risk: Low activity repository, potential sandbox escape vectors documented in issues
- Impact: Security vulnerability in code execution feature
- Migration plan:
  - Monitor for security advisories
  - Consider Google Cloud Functions or AWS Lambda for untrusted execution
  - Implement kernel-level isolation (Docker/seccomp)
  - Implement strict allowlist of operations instead of blacklist

### Gemini Flash API Rate Limiting

**Package: Google Gemini Flash API**

- Risk: Rate limits not clearly documented, API changes could break agent functionality
- Impact: Chat feature becomes unavailable if rate limits exceeded
- Mitigation: Implement retry logic with exponential backoff (partially done), add request queuing
- Alternative: Add fallback to local LLM (Ollama/Llama2)

### SAM Model Availability

**Package: Meta Segment Anything Model**

- Risk: Model download from Meta servers, no fallback if URL breaks
- Impact: Interactive segmentation unavailable, image processing fails
- Migration plan: Cache models in container, implement graceful degradation, add alternative segmentation method

---

## Missing Critical Features

### No Audit Logging for Data Access

**Problem: No comprehensive audit trail for sensitive operations**

- What's missing: Logging user access to experiments, images, cell crops; tracking who deleted what and when
- Blocks: Compliance with data governance policies, incident investigation
- Files to add: `backend/services/audit_service.py`, logging middleware
- Recommendation: Log all data access with user ID, timestamp, operation type

### No Rate Limiting on Image Upload

**Problem: Users can upload unlimited images**

- What's missing: Per-user upload limits, per-experiment image limits
- Blocks: Quota management, cost control in cloud environments
- Files: `backend/routers/images.py` upload endpoint
- Recommendation: Add rate limiting based on user tier, implement soft/hard quotas

### No Image Processing Job Queue Visibility

**Problem: Users unaware of processing status**

- What's missing: Real-time progress tracking for background image processing
- Blocks: User frustration, no way to estimate completion time
- Files: `backend/services/image_processor.py`, WebSocket endpoints needed
- Recommendation: Emit processing events via WebSocket, track sub-task progress

---

## Test Coverage Gaps

### Canvas Coordinate Transformations

**What's not tested: Zoom, pan, rotate operations preserve bbox accuracy**

- Files: `frontend/lib/editor/geometry.ts`, canvas transformation utilities
- Risk: Bbox drawing off-center after zoom/pan leads to incorrect annotations
- Priority: High - directly impacts data quality

### Concurrent Segmentation Requests

**What's not tested: Multiple users requesting segmentation of same image simultaneously**

- Files: `backend/services/segmentation_service.py`
- Risk: Race conditions in embedding cache, model reloading
- Priority: High - could cause crashes or incorrect results

### RAG Document Indexing Edge Cases

**What's not tested: Updating already-indexed documents, malformed PDFs, very large documents**

- Files: `backend/services/document_indexing_service.py`, `backend/services/rag_service.py`
- Risk: Silent failures in indexing, search returning stale results
- Priority: Medium - rare cases but impact user trust in RAG

### Import/Export Format Validation

**What's not tested: Corrupted import files, partial exports, format version mismatches**

- Files: `backend/services/import_service.py`, `backend/services/export_service.py`
- Risk: Data loss, inconsistent state after failed import
- Priority: Medium - could affect reproducibility

### Admin Panel Authorization

**What's not tested: Non-admin users cannot access admin endpoints**

- Files: `backend/routers/admin.py`
- Risk: Information disclosure, unauthorized user modifications
- Priority: Critical - security issue
- Note: Recently added admin panel (PR #19), limited test coverage

---

*Concerns audit: 2026-01-29*
