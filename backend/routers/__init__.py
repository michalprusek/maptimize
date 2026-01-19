"""API routers."""
from fastapi import APIRouter

from .auth import router as auth_router
from .experiments import router as experiments_router
from .images import router as images_router
from .ranking import router as ranking_router
from .proteins import router as proteins_router
from .metrics import router as metrics_router
from .embeddings import router as embeddings_router
from .settings import router as settings_router
from .bug_reports import router as bug_reports_router
from .segmentation import router as segmentation_router
from .export_import import router as export_import_router
from .chat import router as chat_router
from .rag import router as rag_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(experiments_router, prefix="/experiments", tags=["Experiments"])
api_router.include_router(images_router, prefix="/images", tags=["Images"])
api_router.include_router(ranking_router, prefix="/ranking", tags=["Ranking"])
api_router.include_router(proteins_router, prefix="/proteins", tags=["Proteins"])
api_router.include_router(metrics_router, prefix="/metrics", tags=["Metrics"])
api_router.include_router(embeddings_router, prefix="/embeddings", tags=["Embeddings"])
api_router.include_router(settings_router, prefix="/settings", tags=["Settings"])
api_router.include_router(bug_reports_router, prefix="/bug-reports", tags=["Bug Reports"])
api_router.include_router(segmentation_router, prefix="/segmentation", tags=["Segmentation"])
api_router.include_router(export_import_router, prefix="/data", tags=["Export/Import"])
api_router.include_router(chat_router, prefix="/chat", tags=["Chat"])
api_router.include_router(rag_router, prefix="/rag", tags=["RAG"])
