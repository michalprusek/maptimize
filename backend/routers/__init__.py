"""API routers."""
from fastapi import APIRouter

from .auth import router as auth_router
from .experiments import router as experiments_router
from .images import router as images_router
from .ranking import router as ranking_router
from .proteins import router as proteins_router
from .metrics import router as metrics_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(experiments_router, prefix="/experiments", tags=["Experiments"])
api_router.include_router(images_router, prefix="/images", tags=["Images"])
api_router.include_router(ranking_router, prefix="/ranking", tags=["Ranking"])
api_router.include_router(proteins_router, prefix="/proteins", tags=["Proteins"])
api_router.include_router(metrics_router, prefix="/metrics", tags=["Metrics"])
