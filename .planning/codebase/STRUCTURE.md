# Codebase Structure

**Analysis Date:** 2026-01-29

## Directory Layout

```
maptimize/
├── backend/                      # FastAPI backend + ML models
│   ├── main.py                   # FastAPI app initialization and routing
│   ├── config.py                 # Settings from environment variables
│   ├── database.py               # SQLAlchemy async session and schema init
│   ├── run.py                    # Entry point for development
│   ├── routers/                  # API endpoint implementations (15 routers)
│   ├── services/                 # Business logic and integrations
│   ├── models/                   # SQLAlchemy ORM models (14 entity types)
│   ├── schemas/                  # Pydantic validation models (request/response)
│   ├── ml/                       # Machine learning pipelines
│   │   ├── detection/            # YOLOv8 cell detection
│   │   ├── features/             # DINOv2, DINOv3, ESMc encoders for embeddings
│   │   ├── segmentation/         # SAM (Segment Anything) for interactive masking
│   │   └── rag/                  # Qwen VL visual embeddings for documents
│   ├── utils/                    # Helper utilities (security, export, rating algorithms)
│   ├── tests/                    # Pytest unit and integration tests
│   ├── migrations/               # Alembic database migrations
│   ├── scripts/                  # Utility scripts for admin tasks
│   ├── data/                     # Runtime directories
│   │   ├── uploads/              # User-uploaded images (by experiment_id)
│   │   ├── rag_documents/        # PDF rendering cache (pages as PNG)
│   │   └── rag_passages/         # Document passages for semantic search
│   ├── weights/                  # Model weights (YOLOv8 best.pt - NOT in repo)
│   ├── .cache/                   # Hugging Face model cache (auto-downloaded)
│   └── runs/                     # YOLOv8 detection outputs (debug)
│
├── frontend/                     # Next.js 14+ React frontend
│   ├── app/                      # App router directory
│   │   ├── auth/                 # Login/register pages
│   │   ├── chat/                 # RAG chat interface
│   │   ├── dashboard/            # Main dashboard + sub-routes
│   │   ├── editor/               # Interactive cell editor (SAM segmentation)
│   │   ├── admin/                # User management (admin only)
│   │   ├── layout.tsx            # Root layout
│   │   ├── page.tsx              # Home redirect logic
│   │   └── providers.tsx         # React Query + i18n setup
│   ├── components/               # Reusable React components (11 categories)
│   │   ├── chat/                 # Chat UI (MessageBubble, input, etc.)
│   │   ├── editor/               # Image editor (canvas, tools)
│   │   ├── experiment/           # Experiment cards, modals
│   │   ├── ranking/              # Pair comparison interface
│   │   ├── layout/               # AppSidebar, navigation
│   │   ├── ui/                   # Base UI (buttons, inputs, tables)
│   │   ├── shared/               # Shared utilities (loading states, error display)
│   │   ├── visualization/        # UMAP plots, charts
│   │   ├── admin/                # Admin panel components
│   │   ├── metric/               # Metric visualization
│   │   └── export/               # Export functionality
│   ├── lib/                      # Utilities and API client
│   │   ├── api.ts                # HTTP client with auth, error handling
│   │   ├── utils.ts              # Helper functions (classnames, formatting)
│   │   ├── animations.ts         # Framer Motion presets
│   │   └── editor/               # Editor-specific types and utils
│   ├── stores/                   # Zustand state management
│   │   ├── authStore.ts          # User auth state + login/logout
│   │   ├── chatStore.ts          # Chat conversation state
│   │   └── settingsStore.ts      # User preferences
│   ├── hooks/                    # Custom React hooks
│   │   ├── useLocalStorage.ts    # Persist state to localStorage
│   │   ├── useEditorPersistence.ts # Save editor state
│   │   ├── useImagePreloader.ts  # Preload images for fast navigation
│   │   └── useMediaQuery.ts      # Responsive design helpers
│   ├── messages/                 # i18n translation files
│   │   ├── en.json               # English translations
│   │   └── fr.json               # French translations
│   ├── i18n/                     # i18n configuration
│   ├── e2e/                      # Playwright end-to-end tests
│   ├── public/                   # Static assets (images, icons)
│   ├── tsconfig.json             # TypeScript configuration
│   ├── next.config.js            # Next.js configuration
│   └── package.json              # Frontend dependencies
│
├── docker/                       # Docker configuration
│   ├── nginx/                    # Reverse proxy configuration
│   ├── Dockerfile.backend        # Backend image definition
│   └── Dockerfile.frontend       # Frontend image definition
│
├── docker-compose.dev.yml        # Development environment (hot-reload)
├── docker-compose.prod.yml       # Production environment (optimized builds)
├── pyproject.toml                # Backend Python dependencies
├── .env.example                  # Environment variable template
├── CLAUDE.md                     # Project-specific instructions
├── .planning/                    # Planning documents
│   └── codebase/                 # Architecture analysis docs
│
├── scripts/                      # Utility scripts
└── logs/                         # Runtime logs
    └── nginx/                    # Nginx access/error logs

```

## Directory Purposes

**`backend/routers/`:**
- Purpose: API endpoint implementations
- Contains: 15 APIRouter modules handling distinct domains
- Key files:
  - `auth.py`: Login, register, logout
  - `experiments.py`: CRUD for experiments, listing
  - `images.py`: Upload, deletion, status updates
  - `ranking.py`: Pair selection, comparison voting
  - `chat.py`: Message sending, thread management, rate limiting
  - `rag.py`: Document upload, indexing
  - `admin.py`: User management, system stats

**`backend/services/`:**
- Purpose: Business logic and integrations
- Contains: 16 service classes handling specific domains
- Key files:
  - `image_processor.py`: Two-phase image pipeline (upload → detect → crop)
  - `gemini_agent_service.py`: Autonomous Gemini Flash agent with tool execution
  - `rag_service.py`: Document search and retrieval
  - `segmentation_service.py`: SAM-based interactive masking
  - `feature_extractor.py`: Pluggable embedding models (DINO, ESMc, etc.)
  - `export_service.py`: Data export to CSV/Excel
  - `import_service.py`: Data import from external formats
  - `job_manager.py`: Background task scheduling

**`backend/models/`:**
- Purpose: SQLAlchemy ORM entity definitions
- Contains: 14 model classes with relationships
- Key models:
  - `user.py`: User with role (ADMIN/USER)
  - `experiment.py`: Experiment with MAP protein reference
  - `image.py`: Image/Z-stack with embedding, UMAP coords, RAG embedding
  - `cell_crop.py`: Cell crop with embedding, ranking comparisons
  - `chat.py`: ChatThread and ChatMessage with model tracking
  - `rag_document.py`: PDF metadata and page embeddings
  - `ranking.py`: Ranking metrics and pair comparisons (TrueSkill)

**`backend/schemas/`:**
- Purpose: Pydantic request/response validation models
- Contains: Validation models mirroring routers
- Pattern: `[Entity]Create`, `[Entity]Update`, `[Entity]Response`

**`backend/ml/`:**
- Purpose: ML model implementations
- Structure:
  - `detection/`: YOLOv8 cell detection with configurable thresholds
  - `features/`: Feature extractors (DINOv2, DINOv3, ESMc proteins)
  - `segmentation/`: SAM encoder/decoder for mask generation
  - `rag/`: Qwen VL encoder for visual document embeddings

**`frontend/app/`:**
- Purpose: Next.js App Router directory structure
- Pattern: `[route]/page.tsx` = page, `[id]/` = dynamic route segments
- Key routes:
  - `/` → redirect to /dashboard or /auth
  - `/auth` → login/register
  - `/dashboard` → experiments listing
  - `/dashboard/experiments/[id]` → experiment detail + image upload
  - `/dashboard/ranking/[metricId]` → pair comparison interface
  - `/editor/[experimentId]/[imageId]` → interactive SAM editor
  - `/chat` → RAG chat interface
  - `/admin` → admin panel (user management)

**`frontend/components/`:**
- Purpose: Reusable React components
- Organization: By feature domain (chat, editor, experiment, etc.)
- Pattern: Component files export named exports, `index.ts` for barrel imports

**`frontend/lib/`:**
- Purpose: Shared utilities and API client
- Key exports:
  - `api.ts`: Singleton ApiClient with auth, error handling
  - `utils.ts`: Classnames helper, date formatting
  - `editor/`: Types for editor state (SegmentClickPoint, SAMEmbeddingStatus)

**`frontend/stores/`:**
- Purpose: Zustand state management
- Pattern: Create hook per domain, persist middleware for auth
- Domains:
  - `authStore`: User, login/logout, auth check
  - `chatStore`: Current chat thread, messages
  - `settingsStore`: User preferences (language, theme)

**`backend/data/`:**
- Purpose: Runtime data storage
- Subdirectories:
  - `uploads/`: Uploaded images organized by experiment ID
  - `rag_documents/`: PDF rendering cache (pages as PNG)
  - `rag_passages/`: Raw document text for semantic search

## Key File Locations

**Entry Points:**
- `backend/main.py`: FastAPI app with lifespan, CORS, exception handlers
- `backend/run.py`: Development entry point
- `frontend/app/layout.tsx`: Root layout wrapper
- `frontend/app/providers.tsx`: React Query + i18n initialization

**Configuration:**
- `backend/config.py`: Settings loader from environment (Pydantic)
- `backend/database.py`: SQLAlchemy async engine + session factory
- `frontend/next.config.js`: Next.js configuration (i18n, image optimization)

**Core Logic:**
- `backend/database.py`: ORM setup, schema updates, default data seeding
- `backend/services/gemini_agent_service.py`: Autonomous agent with tool registry
- `backend/services/image_processor.py`: Image analysis pipeline
- `backend/ml/detection/detector.py`: YOLOv8 cell detection
- `frontend/lib/api.ts`: HTTP client singleton

**Testing:**
- `backend/tests/`: Pytest test suite
- `frontend/e2e/`: Playwright E2E tests

## Naming Conventions

**Backend Files:**
- Services: `{domain}_service.py` (e.g., `image_processor.py`, `rag_service.py`)
- Models: `{entity}.py` (e.g., `user.py`, `experiment.py`)
- Routers: `{resource}.py` (e.g., `experiments.py`, `images.py`)
- Utilities: `{function}.py` (e.g., `security.py`, `rating.py`)

**Backend Classes:**
- Services: `{Domain}Service` (e.g., `RagService`, `ImageProcessor`)
- Models: `{Entity}` (e.g., `User`, `Experiment`, `CellCrop`)
- Schemas: `{Entity}{Action}` (e.g., `ExperimentCreate`, `ChatMessageResponse`)

**Frontend Files:**
- Pages: `page.tsx` in route directory
- Components: `{Component}.tsx` (e.g., `ChatMessage.tsx`, `ExperimentCard.tsx`)
- Stores: `{domain}Store.ts` (e.g., `authStore.ts`)
- Hooks: `use{HookName}.ts` (e.g., `useLocalStorage.ts`)
- Types: Inline in component files or `lib/editor/types.ts`

**Frontend Naming:**
- Components: PascalCase (e.g., `MessageBubble`, `ExperimentForm`)
- Functions/variables: camelCase (e.g., `handleSubmit`, `imageId`)
- Stores: Named exports with `useXxxStore` hook convention

## Where to Add New Code

**New API Endpoint:**
1. Create route function in `backend/routers/{resource}.py`
2. Create request schema in `backend/schemas/{entity}.py` (if needed)
3. Call service method from `backend/services/{domain}_service.py`
4. Return response schema type

**New Page:**
1. Create directory `frontend/app/{route}/`
2. Add `page.tsx` with client component ("use client")
3. Import components from `frontend/components/`
4. Use `useAuthStore()` for auth checks, React Query for API calls
5. Import translations via `useTranslations()` hook

**New Component:**
1. Create file `frontend/components/{category}/{ComponentName}.tsx`
2. Export named component
3. Add to `frontend/components/{category}/index.ts` barrel export
4. Use `classNames()` utility for conditional styling
5. Import shadcn/ui components from `frontend/components/ui/`

**New Service:**
1. Create `backend/services/{domain}_service.py`
2. Define class with async methods
3. Use dependency injection (pass db session as parameter)
4. Import from `backend/models/` for type hints
5. Call from routers using `Depends(get_db)`

**New ML Model:**
1. Create directory `backend/ml/{domain}/`
2. Define model class in `{model}.py`
3. Implement encoding/decoding as async methods
4. Use lazy loading for model weights (defer until first use)
5. Handle device selection (CPU/GPU)
6. Call from service layer with error handling

**New Database Model:**
1. Create file `backend/models/{entity}.py`
2. Define SQLAlchemy class extending `Base`
3. Add relationships using `relationship()` for foreign keys
4. Add columns with `Column()` and type annotations
5. Database schema updates applied automatically in `database.py:ensure_schema_updates()`

## Special Directories

**`backend/data/uploads/`:**
- Purpose: User-uploaded images
- Organization: `{experiment_id}/{image_id}/` subdirectories
- Generated: Yes (runtime)
- Committed: No (mounted as Docker volume)

**`backend/data/rag_documents/`:**
- Purpose: PDF rendering cache
- Organization: `{user_id}/doc_{id}_pages/` with page PNG files
- Generated: Yes (during PDF upload via service)
- Committed: No

**`backend/weights/`:**
- Purpose: ML model weights
- Contents: `best.pt` (YOLOv8 trained model)
- Generated: No (must be provided externally)
- Committed: No (too large, load from S3 or NFS)
- **CRITICAL:** Backend won't start without this file

**`frontend/.next/`:**
- Purpose: Next.js build cache
- Generated: Yes (during build)
- Committed: No

**`backend/.cache/huggingface/`:**
- Purpose: Hugging Face model cache
- Generated: Yes (auto-downloaded on first use)
- Committed: No
- Models: DINOv2, DINOv3, ESMc, Qwen VL encoder

**`backend/migrations/`:**
- Purpose: Alembic database migration scripts
- Generated: No (created manually via alembic CLI)
- Committed: Yes
- Usage: Run via `alembic upgrade head` before deployment

