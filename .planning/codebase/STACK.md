# Technology Stack

**Analysis Date:** 2026-01-29

## Languages

**Primary:**
- Python 3.12 - Backend (FastAPI) and ML services
- TypeScript 5.7 - Frontend (Next.js) with strict type checking
- JavaScript (Node.js 20) - Frontend build and runtime

**Secondary:**
- SQL - PostgreSQL queries and schema
- Shell/Bash - Docker and deployment scripts
- YAML - Configuration files (docker-compose)

## Runtime

**Environment:**
- Backend: Python 3.12-slim (containerized)
- Frontend: Node.js 20-alpine (containerized)
- Database: PostgreSQL 16 with pgvector extension (containerized)
- Cache: Redis 7-alpine (containerized)

**Package Managers:**
- Backend: pip/uv (with pyproject.toml)
- Frontend: npm (with package-lock.json)
- Lockfile: Both present and committed

## Frameworks

**Core:**
- FastAPI 0.109+ - REST API backend, async request handling
- Next.js 14.2.20 - Frontend framework, React with server components
- React 18.3.1 - UI library for components
- SQLAlchemy 2.0+ - ORM with async support (AsyncSession)

**ML/AI:**
- PyTorch 2.1+ - Deep learning framework (GPU-enabled)
- Ultralytics YOLOv8 8.1+ - Cell detection via trained model (`weights/best.pt`)
- Transformers 4.56+ - DINOv3 for cell embeddings, ESM-C for protein embeddings
- Qwen VL (Vision Language) - Document page embeddings for RAG
- SAM 3 (Segment Anything 3) - Interactive cell segmentation
- TensorFlow - Part of PyTorch ecosystem dependencies

**Visualization:**
- Matplotlib 3.8+ - Chart generation in Python
- Seaborn 0.13+ - Statistical visualizations
- Recharts 2.15+ - React charts library for dashboard

**AI Chat:**
- google-genai 1.55+ - Gemini Flash 3 API (chat agent)
- Gemini 2.0 Flash - LLM for agentic AI with function calling

**Testing:**
- Playwright 1.50+ - E2E testing framework (frontend)
- pytest 7.4+ - Unit testing (backend)
- pytest-asyncio 0.23+ - Async test support

**Build & Dev:**
- Vite (implicit via Next.js) - Frontend bundler
- Webpack - Bundled with Next.js
- Tailwind CSS 3.4.17 - Utility-first CSS framework
- PostCSS 8.4.49 - CSS processing (Tailwind)
- autoprefixer 10.4.20 - CSS vendor prefixes
- ESLint 8.57.1 - Code quality (JavaScript/TypeScript)
- TypeScript compiler 5.7.2 - Type checking

## Key Dependencies

**Critical (Backend):**
- asyncpg 0.29+ - PostgreSQL async driver (replaces psycopg2 for async)
- psycopg2-binary 2.9.9 - PostgreSQL sync driver fallback
- sqlalchemy 2.0+ - ORM with async/await support
- pgvector 0.3+ - Vector similarity search for embeddings and RAG
- redis 5.0+ - Session cache, rate limiting
- pydantic 2.5+, pydantic-settings 2.1+ - Data validation and configuration
- alembic 1.13+ - Database migration tool (installed but minimal use)
- python-jose 3.3+ - JWT token creation/verification
- passlib 1.7.4 - Password hashing with bcrypt
- email-validator 2.3+ - Email format validation

**Data Processing & ML:**
- numpy 1.26+ - Numerical computing
- pandas 2.0+ - Data analysis and CSV/Excel export
- scipy 1.12+ - Scientific computing (distance metrics, etc.)
- scikit-learn 1.4+ - Machine learning utilities (UMAP)
- opencv-python-headless 4.9+ - Image processing (no display)
- pillow 10.2+ - Image manipulation
- tifffile 2024.1.30 - TIFF image support

**Code Execution (Safe Sandbox):**
- RestrictedPython 7.0+ - Secure Python code execution (guards malicious operations)
- sqlparse 0.5+ - SQL query validation and parsing

**Web & External APIs:**
- httpx 0.26+ - Async HTTP client (UniProt, PubMed, external APIs)
- beautifulsoup4 4.12+ - HTML/XML parsing for web browsing
- lxml 5.0+ - HTML/XML parsing backend
- pdf2image 1.17+ - PDF to PNG rendering for Vision RAG
- qwen-vl-utils 0.0.8 - Qwen Vision Language utilities

**Frontend Libraries:**
- @tanstack/react-query 5.62+ - Server state management, API caching
- zustand 5.0.2 - Lightweight client state management
- framer-motion 11.15+ - Animation library
- recharts 2.15+ - React chart library
- react-markdown 9.0+ - Markdown rendering
- remark-math, rehype-katex - LaTeX/KaTeX math support
- react-pdf-viewer 3.12+ - PDF document viewer
- react-dropzone 14.3.5 - File upload handling
- lucide-react 0.468+ - Icon library
- date-fns 3.6+ - Date utilities
- class-variance-authority 0.7.1 - CSS class composition
- clsx 2.1.1 - Conditional CSS classes
- tailwind-merge 2.6+ - Merge Tailwind classes intelligently
- next-intl 4.7+ - Internationalization (i18n)

**Infrastructure:**
- uvicorn 0.27+ - ASGI application server for FastAPI
- gunicorn - Not explicitly listed (likely in production entrypoint)
- green-let 3.3+ - Coroutine compatibility library
- aiofiles 23.2.1 - Async file operations
- multipart 0.0.6+ - Multipart form data parsing

## Configuration

**Environment:**
Backend loads from `.env` file via Pydantic Settings:
- `DATABASE_URL` - PostgreSQL connection string with asyncpg driver
- `REDIS_URL` - Redis connection for caching
- `JWT_SECRET` - Secret key for token signing
- `GEMINI_API_KEY` - Google Generative AI API key
- `HF_TOKEN` - HuggingFace token for model access
- `YOLO_MODEL_PATH` - Path to YOLOv8 weights (`weights/best.pt`)
- `YOLO_CONFIDENCE_THRESHOLD`, `YOLO_IOU_THRESHOLD` - Detection parameters
- `ML_MEMORY_LIMIT_GB`, `GPU_MEMORY_FRACTION` - GPU allocation
- `CUDA_VISIBLE_DEVICES` - GPU device selection

Frontend uses environment variables at build time:
- `NEXT_PUBLIC_API_URL` - Frontend API endpoint (empty string - uses `/api/` prefix)
- `INTERNAL_API_URL` - Server-side API URL for Next.js rewrites
- `NODE_ENV` - Build target (development/production)

**Build:**
- `pyproject.toml` - Python project metadata and dependencies
- `package.json` - Node.js project metadata and dependencies
- `tsconfig.json` - TypeScript compiler configuration
- `frontend/next.config.mjs` - Next.js build configuration
- `.env` - Development environment variables (committed with defaults)
- `docker-compose.prod.yml` - Production Docker orchestration
- `docker-compose.dev.yml` - Development Docker setup
- `Dockerfile`, `Dockerfile.dev`, `Dockerfile.gpu` - Container definitions

## Platform Requirements

**Development:**
- Docker 20.10+ - Containerization
- NVIDIA Docker runtime - GPU support
- Git 2.30+ - Version control
- Python 3.12 - For local development (optional if using Docker)
- Node.js 20 - For local frontend development (optional if using Docker)
- 24GB GPU VRAM total (split with Spheroseg):
  - Maptimize: 16GB (production), 8GB (development)
  - Spheroseg: 8GB (reserved)
  - Total capacity: RTX A5000 (24GB)

**Production:**
- Deployment: Docker Compose on UTIA server (Kubernetes optional)
- Database: PostgreSQL 16 with pgvector extension
- Cache: Redis 7+
- Reverse proxy: Nginx (Alpine)
- GPU: NVIDIA A5000 with CUDA 12.6+ support
- SSL: Let's Encrypt certificates via nginx
- Disk: ~100GB (model weights, uploads, rag documents)
- Memory: 16GB RAM minimum
- CPU: 8+ cores recommended

**Networking:**
- Port range: 7000-7443 (frontend, backend, db, redis, nginx)
- Integration: Reverse proxy via nginx-main (80/443) on domain routing
- URL: `maptimize.utia.cas.cz` routes to `maptimize-nginx:7080`

---

*Stack analysis: 2026-01-29*
