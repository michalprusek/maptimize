# Architecture

**Analysis Date:** 2026-01-29

## Pattern Overview

**Overall:** Full-stack monolith with clear separation between FastAPI backend (Python), Next.js frontend (TypeScript), and ML pipeline (PyTorch/YOLOv8).

**Key Characteristics:**
- RESTful API with `/api/*` routes prefixed
- Async-first architecture (FastAPI + asyncio, React Server Components)
- Multi-tenant with user-based data isolation
- Heavy ML integration (cell detection, embeddings, vector search)
- Vision RAG system for document understanding
- Autonomous Gemini agent with tool-based execution

## Layers

**API Layer (Backend Routers):**
- Purpose: HTTP endpoints for frontend consumption
- Location: `backend/routers/`
- Contains: 15 routers handling auth, experiments, images, ranking, chat, RAG, admin, etc.
- Depends on: Database (SQLAlchemy), Services, Security utilities
- Used by: Frontend via fetch/React Query

**Service Layer (Business Logic):**
- Purpose: Encapsulates domain logic, ML pipelines, external integrations
- Location: `backend/services/`
- Contains: Image processing, embeddings, segmentation, RAG indexing, export/import, job scheduling
- Depends on: Database, ML models, External APIs (Gemini, UniProt)
- Used by: Routers, other services

**Data Layer (Models + Database):**
- Purpose: SQLAlchemy ORM models and database session management
- Location: `backend/models/` (entities), `backend/database.py` (session/init)
- Contains: User, Experiment, Image, CellCrop, MapProtein, Ranking, Chat, RAGDocument, etc.
- Depends on: PostgreSQL + pgvector, SQLAlchemy async
- Used by: All routers and services

**ML Pipeline (Detection, Embeddings, Segmentation):**
- Purpose: Computer vision operations for cell analysis
- Location: `backend/ml/`
- Contains: YOLOv8 detection, DINOv2/DINOv3/ESMc embeddings, SAM segmentation, Qwen VL encoding
- Depends on: GPU, PyTorch, model weights
- Used by: Image processor service, visualization service

**Frontend Layer (UI/UX):**
- Purpose: React/Next.js UI for user interactions
- Location: `frontend/app/`, `frontend/components/`
- Contains: Pages, components, hooks, state management
- Depends on: API client, authentication, Zustand stores
- Used by: End users

**Schema/Validation Layer:**
- Purpose: Pydantic models for request/response validation
- Location: `backend/schemas/`
- Contains: ExperimentCreate, ChatMessageResponse, RankingUpdate, etc.
- Depends on: Pydantic v2
- Used by: All routers for type safety

## Data Flow

**Image Upload and Processing:**

1. User uploads image via `POST /api/images/upload` (ExperimentId provided)
2. FastAPI stores file in `data/uploads/{experiment_id}/`
3. ImageProcessor service (async) called:
   - Phase 1: Load image/Z-stack, create MIP/SUM projections, thumbnail → database
   - Phase 2 (background): Run YOLOv8 detection → create CellCrop records
4. Each CellCrop gets embedding from DINOv2/v3 model (background task)
5. Embeddings stored as pgvector (1024-dim) in `cell_crops.embedding`
6. UMAP computed for visualization (pre-computed coordinates stored)

**Chat with RAG (Vision-based):**

1. User uploads PDF via `/api/rag/upload`
2. PDF pages rendered as PNG images (150 DPI)
3. Qwen VL encoder creates visual embeddings (2048-dim)
4. Embeddings stored in `images.rag_embedding` (pgvector)
5. User sends chat message → triggers Gemini Flash agent
6. Agent calls `search_documents` tool → semantic search via pgvector
7. Document pages (as base64 images) sent to Gemini Vision
8. Gemini Vision reads image directly, generates response
9. Chat thread/message stored in database with model version

**Ranking Workflow:**

1. User creates ranking metric (e.g., "cell size quality")
2. Metric defines pair selection strategy + TrueSkill parameters
3. Backend generates "interesting" pairs from CellCrop pool
4. User votes on pairs → comparisons stored in database
5. TrueSkill algorithm updates rankings via `update_rankings` job
6. Pre-computed UMAP coordinates update based on ranking

**State Management:**

- **Backend:** SQLAlchemy ORM with async sessions (get_db dependency injection)
- **Frontend:** Zustand stores (authStore, chatStore, settingsStore) for client state
- **Chat History:** Persisted in database (ChatThread, ChatMessage models)
- **Agent Memory:** Long-term context via AgentMemory model (user_id indexed)
- **Embeddings Cache:** In-memory StatsCache for rapid queries (1-min TTL)

## Key Abstractions

**ImageProcessor:**
- Purpose: Unified API for two-phase image analysis
- Examples: `backend/services/image_processor.py`
- Pattern: Async class with `process_upload_only()` and `process_with_detection()`

**GeminiAgentService:**
- Purpose: Autonomous agent with tool-calling capability
- Examples: `backend/services/gemini_agent_service.py`
- Pattern: Tool registry (google_search, execute_python_code, query_database, etc.) + streaming response handler

**FeatureExtractor:**
- Purpose: Unified embedding interface supporting multiple models
- Examples: `backend/ml/features/feature_extractor.py`
- Pattern: Pluggable encoders (DINOv2, DINOv3, ESMc with factory method)

**SAMFactory:**
- Purpose: Unified segmentation API supporting SAM1, SAM3, mobile variants
- Examples: `backend/ml/segmentation/sam_factory.py`
- Pattern: Factory pattern with lazy model loading and device handling

**RAGService:**
- Purpose: Document retrieval via semantic search + vision-based content extraction
- Examples: `backend/services/rag_service.py`
- Pattern: Search document metadata (pgvector) → retrieve pages as base64 images

**ApiClient (Frontend):**
- Purpose: Centralized HTTP client with auth, error handling, JSON serialization
- Examples: `frontend/lib/api.ts`
- Pattern: Singleton with token management via localStorage

## Entry Points

**Backend Server:**
- Location: `backend/main.py`
- Triggers: Docker container startup or `python -m uvicorn backend.main:app`
- Responsibilities: FastAPI app initialization, lifespan management (DB init, cleanup), CORS, exception handlers, router mounting

**Frontend Root:**
- Location: `frontend/app/layout.tsx` + `frontend/app/providers.tsx`
- Triggers: Next.js route initialization
- Responsibilities: Global layout, React Query provider setup, i18n provider wrapping

**Chat Page:**
- Location: `frontend/app/chat/page.tsx`
- Entry point for RAG-powered conversations
- Calls `/api/chat/send` with user message + context

**Dashboard:**
- Location: `frontend/app/dashboard/page.tsx`
- Home page after login, lists experiments
- Calls `/api/experiments` for listing

**Ranking Page:**
- Location: `frontend/app/dashboard/ranking/[metricId]/page.tsx`
- Interactive pair comparison interface
- Calls `/api/ranking/pairs` and `/api/ranking/comparisons`

## Error Handling

**Strategy:** Layered error handling with specific HTTP status codes and JSON error payloads.

**Patterns:**
- **Request validation errors** → 400 with `ValidationError` details from Pydantic
- **Authentication failures** → 401 with "Unauthorized" detail
- **Authorization failures** → 403 with "Forbidden" detail
- **Resource not found** → 404 with "Not found" detail (e.g., "Experiment not found")
- **Conflict errors** → 409 with context (e.g., cascade delete warning)
- **Rate limit exceeded** → 429 with remaining quota info (Redis-based)
- **Unhandled exceptions** → 500 with generic "Internal server error" (full traceback logged)

**Frontend handling:**
- HTTP errors caught in `ApiClient.request()` and wrapped as `Error`
- React Query retries failed requests with exponential backoff (1 retry by default)
- Error toast notifications via UI layer

## Cross-Cutting Concerns

**Logging:**
- Backend: Python `logging` module configured per module (logger = logging.getLogger(__name__))
- Frontend: Console.log/console.error for development
- All API calls logged with endpoint + error context

**Validation:**
- Backend: Pydantic schemas in `backend/schemas/`
- Frontend: Controlled components with client-side form validation (HTML5 + custom)
- Database: NOT NULL constraints + foreign keys enforced at schema level

**Authentication:**
- JWT tokens issued at login (`/api/auth/login`) with 24-hour expiry
- Token stored in localStorage (frontend) and Authorization header
- Per-request validation via `get_current_user` dependency in routers
- Admin role check via `UserRole.ADMIN` enum

**Authorization:**
- User-based data isolation: All queries filtered by `current_user.id`
- Admin-only endpoints protected with role check
- Experiment/image ownership enforced at route level (404 if not owner)

**Rate Limiting:**
- Chat endpoints: 10 requests/minute per user (Redis sorted-set based)
- Uses sliding window algorithm for production-ready distributed limiting
- Returns 429 with rate limit info if exceeded

