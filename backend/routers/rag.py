"""RAG (Retrieval-Augmented Generation) API routes.

Handles document upload, indexing, and search operations.
"""
import logging
import time
from pathlib import Path
from typing import List, Optional

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from database import get_db
from models.user import User
from models.rag_document import RAGDocument, RAGDocumentPage, DocumentStatus
from schemas.chat import (
    RAGDocumentUploadResponse,
    RAGDocumentResponse,
    RAGDocumentPageResponse,
    RAGIndexingStatusResponse,
    RAGSearchResponse,
)
from utils.security import get_current_user, get_current_user_from_query
from services.document_indexing_service import (
    save_uploaded_document,
    process_document_async,
    delete_document,
    get_indexing_status,
    is_supported_file,
    reindex_document,
)
from services.rag_service import (
    search_documents,
    search_fov_images,
    combined_search,
    batch_index_fov_images,
)

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


# ============== Upload Rate Limiting ==============
# Limit: 10 uploads per hour per user (prevent disk/GPU exhaustion)
UPLOAD_RATE_LIMIT_REQUESTS = 10
UPLOAD_RATE_LIMIT_WINDOW = 3600  # 1 hour in seconds

_redis_pool: Optional[redis.Redis] = None


async def _get_redis() -> redis.Redis:
    """Get Redis connection (lazy initialization)."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
        )
    return _redis_pool


async def _check_upload_rate_limit(user_id: int) -> None:
    """
    Check if user has exceeded upload rate limit.

    Prevents disk space and GPU exhaustion from excessive uploads.
    Raises HTTPException 429 if rate limit exceeded.
    """
    try:
        r = await _get_redis()
        key = f"rate_limit:upload:{user_id}"
        now = time.time()
        window_start = now - UPLOAD_RATE_LIMIT_WINDOW

        async with r.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            results = await pipe.execute()

        request_count = results[1]

        if request_count >= UPLOAD_RATE_LIMIT_REQUESTS:
            oldest_entries = await r.zrange(key, 0, 0, withscores=True)
            if oldest_entries:
                oldest_ts = oldest_entries[0][1]
                retry_after = int(oldest_ts + UPLOAD_RATE_LIMIT_WINDOW - now) + 1
            else:
                retry_after = UPLOAD_RATE_LIMIT_WINDOW

            logger.warning(f"Upload rate limit exceeded for user {user_id}: {request_count} uploads in 1 hour")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Upload rate limit exceeded. Maximum {UPLOAD_RATE_LIMIT_REQUESTS} uploads per hour.",
                headers={"Retry-After": str(retry_after)},
            )

        # Record this upload
        import uuid
        member = f"{now}:{uuid.uuid4().hex[:8]}"
        await r.zadd(key, {member: now})
        await r.expire(key, UPLOAD_RATE_LIMIT_WINDOW + 60)

    except redis.RedisError as e:
        # Fail-open if Redis is unavailable
        logger.warning(f"Redis upload rate limit check failed: {e}")


async def get_document_for_user(
    db: AsyncSession,
    document_id: int,
    user_id: int
) -> RAGDocument:
    """Get RAG document and verify ownership. Raises 404 if not found."""
    result = await db.execute(
        select(RAGDocument).where(
            RAGDocument.id == document_id,
            RAGDocument.user_id == user_id
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    return document


# ============== Document Management ==============

@router.get("/documents", response_model=List[RAGDocumentResponse])
async def list_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's uploaded documents."""
    query = select(RAGDocument).where(RAGDocument.user_id == current_user.id)

    if status_filter:
        query = query.where(RAGDocument.status == status_filter)

    query = query.order_by(RAGDocument.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    documents = result.scalars().all()

    return [RAGDocumentResponse.model_validate(doc) for doc in documents]


@router.post("/documents/upload", response_model=RAGDocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a document for RAG indexing.

    Supported formats: PDF, DOCX, PPTX, XLSX, images.
    Documents are processed asynchronously in the background.

    Rate limited: max 10 uploads per hour per user.
    """
    # Check rate limit before processing upload
    await _check_upload_rate_limit(current_user.id)

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required"
        )

    if not is_supported_file(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Supported: PDF, DOCX, PPTX, XLSX, images"
        )

    # Check file size (max 100MB)
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large. Maximum size is 100MB"
        )

    try:
        # Save document
        document = await save_uploaded_document(
            user_id=current_user.id,
            filename=file.filename,
            content=content,
            db=db,
        )
        await db.commit()

        # Process document in background
        background_tasks.add_task(process_document_async, document.id)

        logger.info(f"Queued document {document.id} for processing")

        return RAGDocumentUploadResponse.model_validate(document)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/documents/{document_id}", response_model=RAGDocumentResponse)
async def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get document details and processing status."""
    document = await get_document_for_user(db, document_id, current_user.id)
    return RAGDocumentResponse.model_validate(document)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document_endpoint(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a document and all associated data."""
    deleted = await delete_document(document_id, current_user.id, db)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )


@router.post("/documents/{document_id}/reindex")
async def reindex_document_endpoint(
    document_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Re-index an existing document.

    Useful for fixing failed indexing or updating embeddings after model changes.
    """
    # Verify document exists and belongs to user
    document = await get_document_for_user(db, document_id, current_user.id)

    # Run reindex in background
    async def run_reindex():
        result = await reindex_document(document_id, current_user.id)
        logger.info(f"Reindex result for document {document_id}: {result}")

    background_tasks.add_task(run_reindex)

    return {
        "status": "reindexing",
        "document_id": document_id,
        "message": "Document re-indexing started in background"
    }


@router.get("/documents/{document_id}/pdf")
async def serve_pdf(
    document_id: int,
    current_user: User = Depends(get_current_user_from_query),
    db: AsyncSession = Depends(get_db)
):
    """Serve the original PDF file for the PDF viewer.

    Uses query parameter token auth for browser-native file access.
    """
    document = await get_document_for_user(db, document_id, current_user.id)

    # Only serve PDF files
    if document.file_type != "pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document is not a PDF"
        )

    pdf_path = Path(document.original_path)
    if not pdf_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PDF file not found"
        )

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=document.name,
    )


@router.get("/documents/{document_id}/pages", response_model=List[RAGDocumentPageResponse])
async def list_document_pages(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all pages of a document."""
    document = await get_document_for_user(db, document_id, current_user.id)

    result = await db.execute(
        select(RAGDocumentPage)
        .where(RAGDocumentPage.document_id == document_id)
        .order_by(RAGDocumentPage.page_number)
    )
    pages = result.scalars().all()

    return [
        RAGDocumentPageResponse(
            id=page.id,
            document_id=page.document_id,
            page_number=page.page_number,
            image_path=page.image_path,
            has_embedding=page.embedding is not None,
        )
        for page in pages
    ]


@router.get("/documents/{document_id}/pages/{page_number}/image")
async def serve_page_image(
    document_id: int,
    page_number: int,
    current_user: User = Depends(get_current_user_from_query),
    db: AsyncSession = Depends(get_db)
):
    """Serve a rendered page image.

    Uses query parameter token auth for browser-native image loading.
    """
    document = await get_document_for_user(db, document_id, current_user.id)

    result = await db.execute(
        select(RAGDocumentPage).where(
            RAGDocumentPage.document_id == document_id,
            RAGDocumentPage.page_number == page_number
        )
    )
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )

    image_path = Path(page.image_path)
    if not image_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page image not found"
        )

    return FileResponse(
        path=image_path,
        media_type="image/png",
    )


# ============== Indexing Status ==============

@router.get("/indexing/status", response_model=RAGIndexingStatusResponse)
async def get_indexing_status_endpoint(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get global indexing status for the current user."""
    status_data = await get_indexing_status(current_user.id, db)
    return RAGIndexingStatusResponse(**status_data)


@router.post("/index/experiment/{experiment_id}")
async def trigger_fov_indexing(
    experiment_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Trigger RAG indexing for all FOV images in an experiment."""
    result = await batch_index_fov_images(experiment_id, current_user.id, db)

    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["error"]
        )

    return result


# ============== Search ==============

@router.get("/search", response_model=RAGSearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=1000, description="Search query"),
    experiment_id: Optional[int] = Query(None, description="Filter FOV to specific experiment"),
    doc_limit: int = Query(10, ge=1, le=50),
    fov_limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Search documents and FOV images using semantic similarity.

    Returns ranked results from both uploaded documents and microscopy images.
    """
    results = await combined_search(
        query=q,
        user_id=current_user.id,
        db=db,
        experiment_id=experiment_id,
        doc_limit=doc_limit,
        fov_limit=fov_limit,
    )

    return RAGSearchResponse(
        query=results["query"],
        documents=[
            {
                "document_id": doc["document_id"],
                "document_name": doc["document_name"],
                "page_number": doc["page_number"],
                "image_path": doc["page_image_url"],  # Field name from rag_service
                "score": doc["similarity_score"],  # Field name from rag_service
            }
            for doc in results["documents"]
        ],
        fov_images=[
            {
                "image_id": img["image_id"],
                "experiment_id": img["experiment_id"],
                "experiment_name": img["experiment_name"],
                "original_filename": img["original_filename"],
                "thumbnail_path": img["thumbnail_path"],
                "score": img["similarity_score"],  # Field name from rag_service
            }
            for img in results["fov_images"]
        ],
    )


@router.get("/search/documents")
async def search_documents_only(
    q: str = Query(..., min_length=1, max_length=1000),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Search only uploaded documents."""
    results = await search_documents(q, current_user.id, db, limit=limit)
    return {"query": q, "results": results}


@router.get("/search/fov")
async def search_fov_only(
    q: str = Query(..., min_length=1, max_length=1000),
    experiment_id: Optional[int] = None,
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Search only FOV images."""
    results = await search_fov_images(
        q, current_user.id, db,
        experiment_id=experiment_id,
        limit=limit
    )
    return {"query": q, "results": results}


@router.get("/documents/{document_id}/search")
async def search_within_document(
    document_id: int,
    q: str = Query(..., min_length=1, max_length=500, description="Text to search for"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Search for text within a specific document's pages (Ctrl+F style).

    Returns all pages containing the search query with match positions.
    Case-insensitive search.
    """
    # Verify document belongs to user
    document = await get_document_for_user(db, document_id, current_user.id)

    # Search in all pages of the document
    query_lower = q.lower()
    result = await db.execute(
        select(RAGDocumentPage)
        .where(RAGDocumentPage.document_id == document_id)
        .order_by(RAGDocumentPage.page_number)
    )
    pages = result.scalars().all()

    matches = []
    for page in pages:
        if page.extracted_text:
            text_lower = page.extracted_text.lower()
            if query_lower in text_lower:
                # Find all match positions
                positions = []
                start = 0
                while True:
                    pos = text_lower.find(query_lower, start)
                    if pos == -1:
                        break
                    positions.append(pos)
                    start = pos + 1

                # Get context snippet around first match
                first_pos = positions[0]
                snippet_start = max(0, first_pos - 50)
                snippet_end = min(len(page.extracted_text), first_pos + len(q) + 50)
                snippet = page.extracted_text[snippet_start:snippet_end]
                if snippet_start > 0:
                    snippet = "..." + snippet
                if snippet_end < len(page.extracted_text):
                    snippet = snippet + "..."

                matches.append({
                    "page_number": page.page_number,
                    "match_count": len(positions),
                    "snippet": snippet,
                })

    return {
        "query": q,
        "document_id": document_id,
        "total_matches": sum(m["match_count"] for m in matches),
        "pages_with_matches": len(matches),
        "matches": matches,
    }
