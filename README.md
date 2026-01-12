# MAPtimize

**Microtubule Analysis Platform** - A web application for biologists to analyze microscopic images of microtubules and MAPs (Microtubule-Associated Proteins).

![MAPtimize Logo](frontend/public/logo.svg)

## Features

- **Cell Detection** - YOLOv8-powered automated cell detection from microscopy images
- **TrueSkill Ranking** - Pairwise comparison system using Plackett-Luce model (OpenSkill)
- **Feature Extraction** - DINOv2 embeddings for cell crop analysis
- **Bundleness Metrics** - Quantitative measurement of microtubule bundling
- **Z-Stack Support** - Process 3D TIFF files with MIP (Maximum Intensity Projection)

## Tech Stack

### Backend
- **Framework:** FastAPI with async SQLAlchemy
- **Database:** PostgreSQL with pgvector extension
- **Auth:** JWT tokens with bcrypt password hashing
- **ML:** PyTorch, Ultralytics (YOLO), transformers (DINOv2)
- **Package Manager:** uv

### Frontend
- **Framework:** Next.js 14 (App Router)
- **Styling:** Tailwind CSS with custom "Cellular Luminescence" theme
- **State:** Zustand + TanStack Query
- **Animations:** Framer Motion

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Git

### Development (Recommended)

```bash
# Clone the repository
git clone https://github.com/michalprusek/maptimize.git
cd maptimize

# Start all services with hot reload
docker-compose -f docker-compose.dev.yml up
```

The application will be available at:
- **Frontend:** http://localhost:3000
- **Backend API:** http://localhost:8000
- **API Docs:** http://localhost:8000/docs

### Production

```bash
# Set environment variables
export JWT_SECRET=your-secure-secret-key

# Start production stack
docker-compose up -d
```

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
│   ├── services/            # Business logic
│   └── ml/                  # ML modules (detection, features)
│
├── frontend/
│   ├── app/                 # Next.js App Router pages
│   ├── components/          # React components
│   ├── lib/                 # API client, utilities
│   └── stores/              # Zustand state stores
│
├── docker-compose.yml       # Production deployment
└── docker-compose.dev.yml   # Development with hot reload
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/register` | POST | Create account |
| `/api/auth/login` | POST | Login (OAuth2) |
| `/api/auth/me` | GET | Current user |
| `/api/experiments` | GET/POST | List/Create experiments |
| `/api/images/upload` | POST | Upload image |
| `/api/ranking/pair` | GET | Get comparison pair |
| `/api/ranking/compare` | POST | Submit comparison |
| `/api/ranking/leaderboard` | GET | Get rankings |

Full API documentation available at `/docs` when running the server.

## Key Concepts

### TrueSkill Ranking
The ranking uses Plackett-Luce model for pairwise comparisons:
- `mu`: Mean skill estimate (default: 25.0)
- `sigma`: Uncertainty (default: 8.333)
- `ordinal_score = mu - 3 * sigma`

### Bundleness Score
Measures microtubule bundling from intensity distribution:
```
bundleness = 0.7071 * z_skewness + 0.7071 * z_kurtosis
```

### MAP Proteins
Default proteins: PRC1, Tau4R, MAP2d, MAP9, EML3, HMMR

## Environment Variables

Create a `.env` file in the `backend/` directory:

```bash
# Required for production
JWT_SECRET=your-secure-secret-key

# Database (defaults work for Docker setup)
DATABASE_URL=postgresql://maptimize:password@localhost:5432/maptimize

# Optional
DEBUG=false
REDIS_URL=redis://localhost:6379
```

## Development

### Manual Setup (without Docker)

```bash
# Backend
cd backend
uv sync
uv run uvicorn backend.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev

# Database (requires Docker)
docker run -d --name maptimize-db \
  -e POSTGRES_USER=maptimize \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=maptimize \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

### Running Tests

```bash
# Backend
cd backend && uv run pytest

# Frontend
cd frontend && npm run test
```

## Design System

The UI uses a dark "Cellular Luminescence" theme optimized for microscopy work:

| Color | Hex | Usage |
|-------|-----|-------|
| Primary (GFP Teal) | `#00d4aa` | Actions, highlights |
| Background | `#0a0f14` | Main background |
| Accent Pink (Cy5) | `#e91e8c` | Secondary highlights |
| Accent Amber | `#ffc107` | Warnings |

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built for the microtubule research community
- YOLO detection trained on custom microscopy dataset
- Design inspired by fluorescence microscopy aesthetics
