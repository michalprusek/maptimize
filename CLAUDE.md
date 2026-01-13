# MAPtimize - Claude Instructions

## Project Overview

MAPtimize is a web application for biologists to analyze microscopic images of microtubules and MAPs (Microtubule-Associated Proteins). The platform provides cell detection, feature extraction, pairwise ranking, and metrics analysis.

## Tech Stack

### Backend (Python 3.11+)
- **Framework**: FastAPI with async SQLAlchemy
- **Database**: PostgreSQL with pgvector extension
- **Auth**: JWT tokens with bcrypt password hashing
- **ML**: PyTorch, Ultralytics (YOLO), transformers (DINOv3)
- **Package Manager**: `uv` (NOT pip)

### Frontend (TypeScript)
- **Framework**: Next.js 14 (App Router)
- **Styling**: Tailwind CSS with custom "Cellular Luminescence" theme
- **State**: Zustand + TanStack Query
- **Animations**: Framer Motion

## Project Structure

```
maptimize/
├── backend/
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Settings (env vars)
│   ├── database.py          # Async SQLAlchemy setup
│   ├── models/              # SQLAlchemy ORM models
│   ├── schemas/             # Pydantic validation schemas
│   ├── routers/             # API route handlers
│   ├── services/            # Business logic (TODO)
│   ├── ml/                  # ML modules
│   │   ├── detection/       # YOLO cell detection
│   │   └── features/        # DINOv3 embeddings, bundleness
│   ├── workers/             # Background tasks (TODO)
│   └── utils/               # Security, helpers
│
├── frontend/
│   ├── app/                 # Next.js App Router pages
│   │   ├── auth/           # Login/Register
│   │   └── dashboard/      # Main application
│   ├── components/          # React components
│   ├── lib/                 # API client, utilities
│   ├── stores/              # Zustand state stores
│   └── hooks/               # Custom React hooks
│
├── data/uploads/            # Uploaded images (gitignored)
├── docker-compose.yml       # Full stack deployment
└── CLAUDE.md               # This file
```

## Key Concepts

### TrueSkill Ranking System
The ranking uses Plackett-Luce model (OpenSkill library) for pairwise comparisons:
- `mu`: Mean skill estimate (default: 25.0)
- `sigma`: Uncertainty (default: 8.333)
- `ordinal_score = mu - 3 * sigma` (conservative estimate)

Two-phase pair selection:
1. **Exploration** (first 50 comparisons): Random pairs
2. **Exploitation**: Uncertainty sampling (highest sigma pairs)

### Bundleness Score (Planned)
Will measure microtubule bundling from intensity distribution. **Note: Not yet implemented in image processing pipeline.**

Formula (to be implemented):
```python
bundleness = 0.7071 * z_skewness + 0.7071 * z_kurtosis

# Z-score parameters (from n=408 dataset):
mean_skewness = 1.1327, std_skewness = 0.4717
mean_kurtosis = 1.0071, std_kurtosis = 1.4920
```

### MAP Proteins
Default proteins in the system:
- PRC1, Tau4R, MAP2d, MAP9, EML3, HMMR

## Data Architecture

### Image and Metric Flow
```
Experiment → Upload Images → Detection → Cell Crops → Import to Metric → Ranking
```

**Key principles:**
1. **Images ONLY exist within experiments** - cannot be uploaded standalone
2. **Upload is on a separate page** - `/dashboard/experiments/[id]/upload`
3. **Experiment detail is a gallery** - `/dashboard/experiments/[id]` shows only images with filters/search/sort
4. **Metrics only IMPORT existing images** - no direct upload to metrics, only import from experiments

### Page Structure

| Page | Path | Purpose |
|------|------|---------|
| Experiments | `/dashboard/experiments` | List all experiments |
| Experiment Detail | `/dashboard/experiments/[id]` | Gallery with filters (no upload) |
| Upload | `/dashboard/experiments/[id]/upload` | Upload images to experiment |
| Metrics | `/dashboard/ranking` | List all metrics |
| Metric Detail | `/dashboard/ranking/[metricId]` | Import, rank, leaderboard (no direct upload) |

### Why This Architecture?
- **Consistent data**: All images go through detection pipeline before ranking
- **MIP/SUM projections**: Z-stack processing happens during upload
- **Metadata**: Cell crops have detection confidence, mean intensity (bundleness planned)
- **Traceability**: Can trace metric images back to source experiments

## Commands

### Development (Docker with hot reload)
```bash
# Start all services with hot reload
docker-compose -f docker-compose.dev.yml up

# Or in background
docker-compose -f docker-compose.dev.yml up -d

# View logs
docker-compose -f docker-compose.dev.yml logs -f backend
docker-compose -f docker-compose.dev.yml logs -f frontend

# Stop all
docker-compose -f docker-compose.dev.yml down
```

### Development (Manual - alternative)
```bash
# Backend
cd backend
uv sync
uv run uvicorn backend.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev

# Database (Docker)
docker run -d --name maptimize-db \
  -e POSTGRES_USER=maptimize \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=maptimize \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

### Production
```bash
docker-compose up -d
```

### Testing
```bash
# Backend tests
cd backend
uv run pytest

# Frontend tests
cd frontend
npm run test
```

## API Endpoints

### Authentication
- `POST /api/auth/register` - Create account
- `POST /api/auth/login` - Login (OAuth2 form)
- `GET /api/auth/me` - Current user

### Experiments
- `GET /api/experiments` - List experiments
- `POST /api/experiments` - Create experiment
- `GET /api/experiments/{id}` - Get details

### Images
- `POST /api/images/upload` - Upload image (multipart)
- `GET /api/images?experiment_id=X` - List images
- `GET /api/images/{id}/file` - Get image file

### Ranking
- `GET /api/ranking/pair` - Get next comparison pair
- `POST /api/ranking/compare` - Submit comparison
- `POST /api/ranking/undo` - Undo last comparison
- `GET /api/ranking/leaderboard` - Get rankings
- `GET /api/ranking/progress` - Convergence stats

### Proteins
- `GET /api/proteins` - List MAP proteins
- `POST /api/proteins` - Add protein

## Design System

### Colors (CSS Variables)
```css
--primary-500: #00d4aa;     /* Fluorescent teal (GFP) */
--bg-primary: #0a0f14;      /* Dark microscope background */
--bg-elevated: #1a242e;     /* Card backgrounds */
--accent-pink: #e91e8c;     /* Cy5 fluorescence */
--accent-amber: #ffc107;    /* Warning/secondary */
```

### Typography
- Display: Outfit (headings)
- Body: IBM Plex Sans
- Mono: JetBrains Mono (metrics, code)

### Components
- `.glass-card` - Glassmorphism cards
- `.btn-primary` - Primary buttons with glow
- `.input-field` - Form inputs
- `.glow-primary` - Glow effect

## Integration with Existing Code

Source code at `/Users/michalprusek/Desktop/microtubules/`:

| Module | Source | Integration Status |
|--------|--------|-------------------|
| Ranking | `reranking_app/backend/` | ✅ Ported |
| Pair Selection | `reranking_app/backend/pair_selection.py` | ✅ Ported |
| YOLO Detection | `detection/train_yolo.py` | ✅ Integrated |
| DINOv3 Encoder | `feature_extraction/encoders/dinov2_encoder.py` | ✅ Integrated |
| Bundleness | `CLAUDE.md` (formula) | ⏳ TODO |

### YOLO Detection Pipeline

The detection pipeline is located at `backend/ml/detection/` and includes:
- `detector.py` - YOLOv8 wrapper with async support
- `weights/best.pt` - Trained weights from microtubules project

**Processing flow:**
1. Upload image → save to disk
2. Background task triggered
3. Load Z-stack TIFF → create MIP
4. Run YOLO detection (conf=0.25, iou=0.7)
5. Crop detected cells → compute basic metrics (mean intensity)
6. Save crops and metrics to database

**Key parameters (from training):**
- IOU threshold: 0.7 (high overlap tolerance)
- Max detections: 100 per image
- Image size: 640px (YOLO input)

### DINOv3 Feature Extraction

The feature extraction pipeline is located at `backend/ml/features/` and includes:
- `dinov3_encoder.py` - DINOv3 Vision Transformer encoder
- `dinov2_encoder.py` - DINOv2 encoder (alternative)
- `feature_extractor.py` - Service for batch processing cell crops
- `base_encoder.py` - Abstract base class with pooling strategies

**Configuration (default):**
- Model: `facebook/dinov3-vitl16-pretrain-lvd1689m` (large variant)
- Embedding dimension: 1024
- Pooling: CLS token
- Batch size: 4 (conservative for large model memory)

**Processing flow:**
1. Cell crops detected → saved to disk
2. Feature extraction triggered (automatic or manual via API)
3. Load MIP images → resize to 224x224
4. Apply ImageNet normalization (mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
5. Extract features via DINOv3 → 1024-dim embeddings
6. Store in database (pgvector) for UMAP visualization

**Requirements:**
- HuggingFace authentication: `huggingface-cli login`
- Accept model terms at: https://huggingface.co/facebook/dinov3-vits16-pretrain-lvd1689m
- transformers >= 4.56 required
- Set `HF_TOKEN` environment variable for Docker

**API endpoints:**
- `GET /api/embeddings/umap` - Get UMAP 2D projection of embeddings
- `GET /api/embeddings/status` - Check extraction progress
- `POST /api/embeddings/extract` - Trigger feature extraction

## Environment Variables

```bash
# Backend (.env)
DATABASE_URL=postgresql://maptimize:password@localhost:5432/maptimize
REDIS_URL=redis://localhost:6379
JWT_SECRET=your-secret-key
DEBUG=true
HF_TOKEN=your-huggingface-token  # Required for DINOv3

# Frontend (.env.local)
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Notes for Development

1. **Use `uv` for Python packages**, not pip
2. **Don't compile LaTeX** - user compiles in Overleaf
3. **Async everywhere** in backend - use `async def` and `await`
4. **Keep UI dark theme** - biologists work in dark rooms
5. **Z-stack support** - images are 3D TIFF files, process as MIP (Maximum Intensity Projection)
6. **ALWAYS use Docker dev with hot reload** - use `docker-compose -f docker-compose.dev.yml up` for development. Since hot reload is enabled, **do NOT restart containers after code changes** - changes are applied automatically
7. **Run code-simplifier after implementation** - after completing any implementation task, always run the `code-simplifier:code-simplifier` agent to refine code for clarity, consistency, and maintainability
8. **Latent space distances** - always use **cosine distance on L2-normalized embeddings** for any similarity/distance computations in latent spaces (UMAP, silhouette score, k-NN, clustering). L2 normalization ensures numerical stability and makes vectors lie on a unit hypersphere.

## Default Admin Setup

When the database is reset, an admin user and default experiment are created automatically.
The credentials are configured via environment variables:

```bash
# Backend environment variables for initial admin setup
DEFAULT_ADMIN_EMAIL=your-email@example.com
DEFAULT_ADMIN_PASSWORD=your-secure-password
```

The admin user is created with the `admin` role, and a default "MAP9 Analysis" experiment is created.

## Docker Operations (Claude MUST do this autonomously)

**IMPORTANT**: Claude is responsible for managing Docker services. User should NOT have to start services manually.

### Check services status
```bash
docker-compose ps
```

### Start all services (if not running)
```bash
docker-compose up -d
```

### Rebuild after code changes
```bash
# Rebuild specific service (e.g., after backend code changes)
docker-compose up -d --build backend

# Rebuild all
docker-compose up -d --build
```

### Health check
```bash
# Quick health check
curl http://localhost:8000/health

# Full health check script
./scripts/health-check.sh
```

### View logs
```bash
# Backend logs
docker logs maptimize-backend-1 --tail 100

# All services
docker-compose logs --tail 50
```

### When to rebuild
- After modifying Python files in `backend/`
- After adding new dependencies to `pyproject.toml`
- After modifying `Dockerfile`

### When NOT to rebuild (hot reload works)
- If using `docker-compose.dev.yml` - changes auto-reload
- Frontend changes (Next.js has its own hot reload)

## TODO

- [x] Integrate YOLO detection worker
- [x] Add DINOv3 feature extraction
- [x] Add UMAP visualization (Recharts)
- [ ] Implement background job queue (Redis)
- [ ] WebSocket for real-time progress updates
- [ ] Export functionality (CSV, images)
- [ ] Admin panel for user management
