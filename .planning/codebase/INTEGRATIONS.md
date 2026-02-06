# External Integrations

**Analysis Date:** 2026-01-29

## APIs & External Services

**AI & Chat:**
- Google Gemini Flash 3 API - Agentic chat with function calling
  - SDK/Client: `google-genai` package
  - Auth: `GEMINI_API_KEY` env var
  - Model: `gemini-3-flash-preview` (primary), `gemini-2.0-flash` (search)
  - Location: `backend/services/gemini_agent_service.py`
  - Features: Tool use, code execution output, function calling, Google Search integration

**Bioinformatics/Life Sciences:**
- UniProt API - Protein sequence and annotation database
  - Base URL: `https://rest.uniprot.org`
  - Auth: None (public API)
  - Endpoints: Protein lookup by ID, sequence search
  - Client: `httpx` async HTTP client
  - Validation: SQL injection prevention via `sqlparse`

- PubMed Literature API - Literature search and metadata
  - Base URL: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils`
  - Auth: None (public API, rate-limited)
  - Endpoints: Literature search, abstract retrieval
  - Client: `httpx` async HTTP client

- Ensembl API - Genome and genomics data
  - Base URL: `https://rest.ensembl.org`
  - Auth: None (public API)
  - Endpoints: Gene lookup, variant info
  - Client: `httpx` async HTTP client

- STRING-DB API - Protein-protein interaction network
  - Auth: None (public API)
  - Client: `httpx` async HTTP client

**Web Search:**
- Google Search (via Gemini) - Real-time web search capability
  - Mechanism: Two-phase approach
    1. Agent makes separate API call with `types.Tool(google_search=types.GoogleSearch())`
    2. Bypasses "Tool use with function calling is unsupported" limitation
  - Used for: Current events, recent research, real-time information
  - Client: `google-genai` with native Google Search tool
  - Model: `gemini-2.0-flash` for stable search responses

**Web Browsing:**
- Generic web pages via `browse_webpage` tool
  - Client: `httpx` async HTTP client with SSRF protection
  - Parsing: `BeautifulSoup4` with `lxml` backend
  - Features: Extract text, links, tables from any URL
  - Security: SSRF blocking for private IPs and reserved ranges

## Data Storage

**Databases:**
- PostgreSQL 16 (pgvector-enhanced)
  - Connection: `postgresql://` URL via `asyncpg` async driver
  - ORM: SQLAlchemy 2.0+ with AsyncSession
  - Extension: pgvector for vector similarity on embeddings
  - Usage: User data, experiments, images, cell crops, rankings, chat messages, agent memory
  - Models: `backend/models/` (user.py, experiment.py, image.py, cell_crop.py, chat.py, agent_memory.py, etc.)
  - Schema: Managed via `database.py` init script + incremental updates
  - Env var: `DATABASE_URL`

**File Storage:**
- Local filesystem (containerized)
  - Path: `/app/data/uploads/` in container (mounted as volume)
  - Usage: Uploaded TIFF images, cell crops, experiment files
  - Shared with nginx for serving static content

**RAG Document Storage:**
- PDF pages as PNG images (Vision RAG approach)
  - Path: `data/rag_documents/{user_id}/doc_{id}_pages/`
  - Format: PNG at 150 DPI (rendered from PDF via `pdf2image`)
  - Indexing: Visual embeddings via Qwen VL encoder
  - Serving: Base64-encoded in API responses to Gemini Vision

**ML Model Weights:**
- YOLOv8 trained model
  - Path: `weights/best.pt` (not in repository - must be provided)
  - Size: ~100MB
  - Usage: Cell detection in images
  - Config: `YOLO_MODEL_PATH` env var

**Caching:**
- Redis 7-alpine
  - Connection: `redis://maptimize-redis:6379` (containerized) or `localhost:6379`
  - Config: 512MB max memory, LRU eviction policy
  - Usage: Session cache, rate limiting, temporary data
  - Env var: `REDIS_URL`
  - Client: `redis` package (async compatible)

## Authentication & Identity

**Auth Provider:**
- Custom JWT-based authentication (internal implementation)
  - JWT Signing: `python-jose` with cryptography backend
  - Algorithm: HS256 (symmetric key signing)
  - Secret: `JWT_SECRET` env var
  - Expiration: `JWT_EXPIRE_MINUTES` (default 1440 = 24 hours)
  - Hashing: bcrypt via `passlib` for password storage
  - Location: `backend/routers/auth.py` (auth endpoints)

**Default Admin:**
- Email: `DEFAULT_ADMIN_EMAIL` (default: `admin@utia.cas.cz`)
- Password: Hashed with bcrypt, set via `DEFAULT_ADMIN_PASSWORD` env var
- Role-based: `UserRole.ADMIN` vs regular users
- Seeded: `database.py::seed_default_data()` on first run

**Email Validation:**
- email-validator package - RFC 5321/5322 compliant validation

## Monitoring & Observability

**Error Tracking:**
- Not detected - No formal error tracking service integrated
- Logging: Python standard logging module to stdout/files

**Logs:**
- Stdout capture via Docker logging driver
- File logs: `./logs/` directory (nginx, application logs)
- Debug mode: Controlled by `DEBUG` env var (FastAPI exception detail)
- Chat/Agent logging: Extensive logging in `backend/services/gemini_agent_service.py` for tool calls, errors

## CI/CD & Deployment

**Hosting:**
- Docker Compose on UTIA server
- Reverse proxy: nginx (Dockerfile image)
- Domains:
  - `maptimize.utia.cas.cz` - Main application
  - Internal routing via nginx-main (ports 80/443) based on domain

**CI Pipeline:**
- GitHub Actions (implied by git workflow references)
- Manual deployment steps documented in `CLAUDE.md`
- E2E tests: Playwright test suite in `frontend/e2e/`
- Backend tests: pytest suite

**Deployment Process:**
```bash
# Production rebuild (not dev!)
docker compose -f docker-compose.prod.yml build maptimize-backend --no-cache
docker compose -f docker-compose.prod.yml up -d maptimize-backend
```

## Environment Configuration

**Required env vars (Production):**
- `DATABASE_URL` - PostgreSQL connection (critical)
- `REDIS_URL` - Redis connection (critical)
- `JWT_SECRET` - Token signing secret (security-critical)
- `GEMINI_API_KEY` - Google AI API key (for chat functionality)
- `HF_TOKEN` - HuggingFace token (for DINOv3, ESM-C models)
- `YOLO_MODEL_PATH` - Path to trained YOLOv8 weights (critical)
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` - Database credentials
- `DEFAULT_ADMIN_EMAIL`, `DEFAULT_ADMIN_PASSWORD` - Initial admin account

**GPU Configuration:**
- `CUDA_VISIBLE_DEVICES` - GPU device ID (default: 0)
- `ML_MEMORY_LIMIT_GB` - Total GPU memory allocation (16GB production, 8GB dev)
- `GPU_MEMORY_FRACTION` - Fraction of GPU memory to use (0.65 production, 0.35 dev)
- `PYTORCH_CUDA_ALLOC_CONF` - PyTorch memory management settings

**Secrets location:**
- `.env` file (committed with defaults, overridden in production)
- Docker Compose: Injected via `environment:` section
- Never stored in image layer (use build args or runtime env vars)
- Production: Environment variables set by deployment system

## Webhooks & Callbacks

**Incoming:**
- None detected - Application is request-response only
- No webhook endpoints for external services

**Outgoing:**
- Google Gemini - Callback structure via function calling (tool responses)
- UniProt/PubMed/Ensembl - One-way HTTP requests, no callbacks
- Web pages (browse_webpage) - One-way HTTP GET requests

## Agentic AI Tool Capabilities

**Agent Tools Available:**
- `query_database` - Execute parameterized SQL queries
- `execute_python_code` - Sandboxed code execution (RestrictedPython)
- `search_documents` - Vector RAG search on uploaded PDFs
- `get_sample_images` - Retrieve experiment images
- `get_segmentation_masks` - Fetch cell segmentation results
- `get_cell_detection_results` - Cell detection statistics
- `export_data` - Export to CSV/Excel
- `call_external_api` - Query approved bioinformatics APIs
- `google_search` - Web search integration
- `browse_webpage` - Fetch and parse web content
- `get_protein_info` - Protein metadata lookup
- `redetect_cells` - Trigger cell detection pipeline
- `create_visualization` - Generate charts

**Tool Validation:**
- SQL queries: Validated via `sqlparse`
- External API calls: Whitelist enforcement (`APPROVED_APIS`)
- Code execution: RestrictedPython guards (no imports, file access, etc.)
- Web browsing: SSRF protection (no private IPs, reserved ranges)

## Document Processing (Vision RAG)

**PDF Handling:**
- Conversion: `pdf2image` (PyTorch, PIL backends) - Renders to PNG at 150 DPI
- Embedding: Qwen VL Vision-Language model encoder
  - Embedding dimension: 2048 (visual features)
  - Storage: pgvector (note: exceeds 2000-dim HNSW/ivfflat index limit)
  - Search: Exact cosine distance (no approximate index)
- Vision Reading: Gemini Vision reads PNG pages directly as base64 images
  - No OCR required
  - Preserves layout, tables, graphs
- Location: `backend/services/document_indexing_service.py`, `backend/services/rag_service.py`

---

*Integration audit: 2026-01-29*
