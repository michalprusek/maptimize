"""RAG (Retrieval-Augmented Generation) API routes.

Handles document upload, indexing, and search operations.
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Annotated, List, Optional

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from database import get_db
from models.user import User
from models.rag_document import (
    RAGDocument, RAGDocumentPage, DocumentStatus, document_scope, document_read_scope,
)
from models.chat import ChatThread
from schemas.chat import (
    RAGDocumentUploadResponse,
    RAGDocumentResponse,
    RAGDocumentPageResponse,
    RAGIndexingStatusResponse,
    RAGSearchResponse,
    DiscoverRequest,
    DiscoveredPaper,
    DiscoverResponse,
    ImportRequest,
    ImportFailure,
    ImportResponse,
)
from utils.groups import get_user_group_id
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
    get_cached_passage,
    image_mime_type,
)
from services.paper_discovery_service import (
    discover as discover_papers,
    fetch_paper_pdf,
    PdfFetchError,
    DiscoveryError,
    search_epmc,
    EPMC_MAX_CONCURRENCY,
    SSRF_REFUSAL_MESSAGE,
    _DOI_RE,
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


async def _check_rate_limit_generic(key: str, limit: int, window: int, count: int = 1) -> None:
    """Sliding-window Redis rate limiter shared by every caller in this router
    (uploads, paper discovery searches, and paper discovery imports).

    ``count`` lets one call consume more than one slot at once (a discovery
    import of N papers spends N of the hourly budget in a single request).
    Fails open if Redis is unavailable -- infra hiccups must not block callers.
    Raises HTTPException 429 if the limit would be exceeded.
    """
    try:
        r = await _get_redis()
        now = time.time()
        window_start = now - window

        async with r.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            results = await pipe.execute()

        request_count = results[1]

        if request_count + count > limit:
            oldest_entries = await r.zrange(key, 0, 0, withscores=True)
            if oldest_entries:
                oldest_ts = oldest_entries[0][1]
                retry_after = int(oldest_ts + window - now) + 1
            else:
                retry_after = window

            logger.warning(
                f"Rate limit exceeded for {key}: {request_count} in window "
                f"(+{count} requested, limit {limit})"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {limit} per {window}s.",
                headers={"Retry-After": str(retry_after)},
            )

        # Record this request (or this many, for a batch call)
        import uuid
        now_members = {f"{now}:{i}:{uuid.uuid4().hex[:8]}": now for i in range(count)}
        await r.zadd(key, now_members)
        await r.expire(key, window + 60)

    except redis.RedisError as e:
        # Fail-open if Redis is unavailable
        logger.warning(f"Redis rate limit check failed for {key}: {e}")


async def _check_upload_rate_limit(user_id: int) -> None:
    """
    Check if user has exceeded upload rate limit.

    Prevents disk space and GPU exhaustion from excessive uploads.
    Raises HTTPException 429 if rate limit exceeded.
    """
    await _check_rate_limit_generic(
        key=f"rate_limit:upload:{user_id}",
        limit=UPLOAD_RATE_LIMIT_REQUESTS,
        window=UPLOAD_RATE_LIMIT_WINDOW,
    )


async def get_document_for_user(
    db: AsyncSession,
    document_id: int,
    user_id: int,
    group_id: Optional[int] = None,
) -> RAGDocument:
    """Get a RAG document the caller may READ. Raises 404 if not visible.

    Read scope = owner's own doc OR a group-shared library doc. Callers that must
    mutate (delete/reindex) must NOT rely on this -- they keep an owner-only check.
    """
    result = await db.execute(
        select(RAGDocument).where(
            RAGDocument.id == document_id,
        ).where(document_read_scope(user_id, group_id))
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
    """List the user's document library.

    Chat attachments are excluded: they belong to their thread, not the library.
    Group-shared library documents from other members are included, marked
    ``is_owner=False``.
    """
    group_id = await get_user_group_id(current_user.id, db)
    query = select(RAGDocument).where(document_scope(current_user.id, None, group_id))

    if status_filter:
        query = query.where(RAGDocument.status == status_filter)

    query = query.order_by(RAGDocument.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    documents = result.scalars().all()

    return [RAGDocumentResponse.for_user(doc, current_user.id) for doc in documents]


@router.post("/documents/upload", response_model=RAGDocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    thread_id: Annotated[Optional[int], Form()] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a document for RAG indexing.

    Supported formats: PDF, DOCX, PPTX, XLSX, images.
    Documents are processed asynchronously in the background.

    If ``thread_id`` is provided the document is a chat attachment scoped to
    that thread (the agent treats it as context for the conversation); otherwise
    it is a document-library upload.

    Rate limited: max 10 uploads per hour per user.
    """
    # Check rate limit before processing upload
    await _check_upload_rate_limit(current_user.id)

    # If this is a chat attachment, verify the thread belongs to the caller so a
    # document cannot be attached to someone else's conversation.
    if thread_id is not None:
        owns = await db.execute(
            select(ChatThread.id).where(
                ChatThread.id == thread_id, ChatThread.user_id == current_user.id)
        )
        if owns.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

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
        document, created = await save_uploaded_document(
            user_id=current_user.id,
            filename=file.filename,
            content=content,
            db=db,
            thread_id=thread_id,
        )
        await db.commit()

        # A duplicate is already stored and already indexed -- re-running the
        # pipeline would burn GPU time to produce the pages it already has.
        if created:
            background_tasks.add_task(process_document_async, document.id)
            logger.info(f"Queued document {document.id} for processing")
        else:
            logger.info(f"Upload of {file.filename} deduplicated to document {document.id}")

        response = RAGDocumentUploadResponse.model_validate(document)
        response.is_duplicate = not created
        return response

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
    group_id = await get_user_group_id(current_user.id, db)
    document = await get_document_for_user(db, document_id, current_user.id, group_id)
    return RAGDocumentResponse.for_user(document, current_user.id)


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
    group_id = await get_user_group_id(current_user.id, db)
    document = await get_document_for_user(db, document_id, current_user.id, group_id)

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
    group_id = await get_user_group_id(current_user.id, db)
    document = await get_document_for_user(db, document_id, current_user.id, group_id)

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
    group_id = await get_user_group_id(current_user.id, db)
    document = await get_document_for_user(db, document_id, current_user.id, group_id)

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
        media_type=image_mime_type(image_path),
    )


@router.get("/documents/{document_id}/passages/{passage_hash}")
async def serve_passage_image(
    document_id: int,
    passage_hash: str,
    current_user: User = Depends(get_current_user_from_query),
    db: AsyncSession = Depends(get_db)
):
    """Serve a cropped passage image from a document page.

    Uses query parameter token auth for browser-native image loading.
    Passages are cached crops extracted by Gemini spatial understanding.
    """
    # Validate passage_hash format (12 char hex)
    if not passage_hash or len(passage_hash) != 12 or not all(c in '0123456789abcdef' for c in passage_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid passage hash format"
        )

    group_id = await get_user_group_id(current_user.id, db)
    passage_path = await get_cached_passage(
        document_id=document_id,
        passage_hash=passage_hash,
        user_id=current_user.id,
        db=db,
        group_id=group_id,
    )

    if not passage_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Passage not found or expired"
        )

    return FileResponse(
        path=passage_path,
        media_type=image_mime_type(passage_path),
    )


# ============== Paper Discovery ==============

# /discover fans out into up to MAX_SUBQUERIES Europe PMC requests per call
# (see paper_discovery_service.discover), so its budget is much tighter than
# the plain-search endpoints and distinct from the import budget below.
DISCOVERY_SEARCH_RATE_LIMIT_REQUESTS = 120
DISCOVERY_SEARCH_RATE_LIMIT_WINDOW = 3600


async def _check_discovery_search_rate_limit(user_id: int) -> None:
    """Sliding-window limiter for /discover searches."""
    await _check_rate_limit_generic(
        key=f"rate_limit:discover:{user_id}",
        limit=DISCOVERY_SEARCH_RATE_LIMIT_REQUESTS,
        window=DISCOVERY_SEARCH_RATE_LIMIT_WINDOW,
    )


@router.post("/discover", response_model=DiscoverResponse)
async def discover_sources(
    payload: DiscoverRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search Europe PMC for papers matching the user's description.

    Returns candidates only — nothing is downloaded here. `importable` reflects
    whether Europe PMC advertises a legally downloadable PDF.
    """
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    await _check_discovery_search_rate_limit(current_user.id)

    try:
        result = await discover_papers(query)
    except DiscoveryError as exc:
        logger.error("Paper discovery search failed: %s", exc)
        detail = "Search service unavailable, please try again"
        if exc.attempted_query:
            # Distinguishes "Europe PMC is down" (retry) from "the rewrite
            # mistranslated your query" (rephrase) -- different remediations.
            detail = f'{detail} (searched as: "{exc.attempted_query}")'
        raise HTTPException(status_code=502, detail=detail) from exc

    papers = result.papers

    # Mark papers already in the caller's readable library (dedupe by DOI,
    # case-insensitively on BOTH sides -- a stored DOI's case can differ from
    # what Europe PMC returns today).
    dois = [p.doi for p in papers if p.doi]
    existing: set[str] = set()
    if dois:
        group_id = await get_user_group_id(current_user.id, db)
        rows = await db.execute(
            select(RAGDocument.doi)
            .where(func.lower(RAGDocument.doi).in_([d.lower() for d in dois]))
            .where(document_scope(current_user.id, None, group_id))
        )
        existing = {d.lower() for d in rows.scalars().all() if d}

    return DiscoverResponse(
        query=query,
        results=[
            DiscoveredPaper(
                doi=p.doi, title=p.title, authors=p.authors, journal=p.journal,
                year=p.year, abstract=(p.abstract or "")[:600] or None,
                source_url=p.source_url,
                importable=p.pdf_url is not None,
                already_imported=bool(p.doi and p.doi.lower() in existing),
            )
            for p in papers
        ],
        failed_queries=result.failed_queries,
        dropped_queries=result.dropped_queries,
        effective_query=result.effective_query,
        rewrite_failed=result.rewrite_failed,
    )


# Discovery imports get their own budget: each one is a user-confirmed,
# open-access PDF from an allow-listed source, not an arbitrary upload, so the
# 10/hour upload cap would be far too tight for a bulk import.
DISCOVERY_RATE_LIMIT_REQUESTS = 1000
DISCOVERY_RATE_LIMIT_WINDOW = 3600

# Hard ceiling on how many papers one /discover/import call will touch, even
# with the rate limiter's budget available -- and even if Redis (and so the
# rate limiter's fail-open path) is down.
MAX_IMPORT_BATCH = 50

# Both the fetch-phase (resolve/download) and the store-phase (disk+DB) except
# blocks report this instead of the raw exception -- internal exception text
# can carry file paths, DB constraint names, etc. and must not reach the
# client. Full detail always goes to logger.exception server-side.
_GENERIC_IMPORT_ERROR = "Could not import this paper due to an internal error"


async def _check_discovery_rate_limit(user_id: int, count: int = 1) -> None:
    """Sliding-window limiter mirroring _check_upload_rate_limit."""
    await _check_rate_limit_generic(
        key=f"rate_limit:discovery_import:{user_id}",
        limit=DISCOVERY_RATE_LIMIT_REQUESTS,
        window=DISCOVERY_RATE_LIMIT_WINDOW,
        count=count,
    )


async def _resolve_paper_by_doi(doi: str):
    """Re-resolve a paper server-side; never trust a client-supplied PDF URL.

    Two independent checks, both required:
    1. ``doi`` must itself look like a DOI (``_DOI_RE.fullmatch``) -- otherwise
       it is interpolated unescaped into ``DOI:"{doi}"`` and a value like
       ``x" OR (OPEN_ACCESS:Y)`` could break out of the quoted term and match
       an arbitrary record.
    2. The record Europe PMC actually returns must carry the SAME doi we asked
       for -- EPMC's search can fuzzy-match, so without this check a client
       could tick one paper and have a different one imported (and its
       provenance wrongly recorded as the paper the user selected).
    """
    if not _DOI_RE.fullmatch(doi):
        return None
    results = await search_epmc(f'DOI:"{doi}"', limit=1)
    paper = results[0] if results else None
    if paper is None or (paper.doi or "").lower() != doi.lower():
        return None
    return paper


def _paper_filename(paper) -> str:
    """A readable, collision-tolerant filename. save_uploaded_document sanitises
    it further and prefixes a timestamp, so this only needs to be human-friendly."""
    # RAGDocument.name is String(255); a consortium authorString with no comma
    # (so .split(",")[0] returns the whole string) could otherwise overflow it
    # and fail the commit.
    first_author = ((paper.authors or "").split(",")[0].strip() or "paper")[:80]
    year = paper.year or "n.d."
    title = (paper.title or "untitled")[:60]
    return f"{first_author} {year} - {title}.pdf"


@router.post("/discover/import", response_model=ImportResponse)
async def import_discovered(
    payload: ImportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import the selected papers into the document library.

    Two phases:
    1. FETCH (resolve on Europe PMC + download the PDF) runs concurrently,
       bounded by EPMC_MAX_CONCURRENCY, so at most that many downloads are
       resident in memory at once.
    2. STORE (write to disk + create the DB row) then runs sequentially over
       whatever fetched successfully, since it shares one DB session.

    A DOI already present in the caller's readable library is skipped before
    it is ever fetched (no duplicate documents from a re-click, a retried 504,
    or two lab members importing the same paper). The PDF is fetched before
    any DB row is created, so a failed download leaves no DB row and no file.
    If the STORE step's own commit then fails, the just-written file is
    deleted so it isn't orphaned -- unless the failure happened *inside*
    save_uploaded_document before it returned a document (see that function's
    docstring: a raise there already leaves a since-documented orphan file,
    which this endpoint cannot reach because it has no path to delete yet).
    """
    raw_dois = [d.strip() for d in (payload.dois or []) if d and d.strip()]
    dois = list(dict.fromkeys(raw_dois))  # de-dupe the request itself, keep order
    if not dois:
        raise HTTPException(status_code=400, detail="No papers selected")
    if len(dois) > MAX_IMPORT_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Too many papers selected (max {MAX_IMPORT_BATCH} per import)",
        )
    await _check_discovery_rate_limit(current_user.id, count=len(dois))

    failures: list[ImportFailure] = []

    # Skip DOIs already in the caller's readable library -- checked against the
    # SAME scope /discover used to mark them "already_imported".
    group_id = await get_user_group_id(current_user.id, db)
    existing_rows = await db.execute(
        select(RAGDocument.doi)
        .where(func.lower(RAGDocument.doi).in_([d.lower() for d in dois]))
        .where(document_scope(current_user.id, None, group_id))
    )
    already_in_library = {d.lower() for d in existing_rows.scalars().all() if d}

    to_fetch = []
    for doi in dois:
        if doi.lower() in already_in_library:
            failures.append(ImportFailure(doi=doi, reason="Already in your library"))
        else:
            to_fetch.append(doi)

    semaphore = asyncio.Semaphore(EPMC_MAX_CONCURRENCY)

    async def fetch_one(doi: str):
        async with semaphore:
            paper = await _resolve_paper_by_doi(doi)
            if paper is None:
                raise PdfFetchError("Not found in Europe PMC")
            # Server-side re-verification, unchanged: no Europe PMC PDF entry
            # means the picker showed this as paywalled, and the fallback chain
            # must NOT quietly promote it to importable. The fallbacks exist to
            # rescue a paper whose advertised PDF link is dead -- not to widen
            # what counts as freely available.
            if not paper.pdf_urls:
                raise PdfFetchError("No freely downloadable PDF for this paper")
            return paper, await fetch_paper_pdf(paper)

    async def fetch_or_capture(doi: str):
        """Never let one paper's exception cancel the sibling gather()s --
        capture it and report per-paper, same as the old sequential loop did."""
        try:
            return await fetch_one(doi)
        except PdfFetchError as e:
            return e
        except Exception as e:
            logger.exception("Discovery fetch failed for %s", doi)
            return e

    fetched = await asyncio.gather(*(fetch_or_capture(doi) for doi in to_fetch))

    imported = 0
    already_in_library: list[str] = []
    for doi, outcome in zip(to_fetch, fetched):
        if isinstance(outcome, PdfFetchError):
            # The most common failure path (paywalled / not found / SSRF
            # refusal) -- must not log silently, or "why did this import only
            # get 6 of 10" is undebuggable.
            if str(outcome) == SSRF_REFUSAL_MESSAGE:
                logger.warning("Discovery import refused for %s: %s", doi, outcome)
            else:
                logger.info("Discovery import skipped %s: %s", doi, outcome)
            failures.append(ImportFailure(doi=doi, reason=str(outcome)))
            continue
        if isinstance(outcome, Exception):
            # Already logger.exception'd inside fetch_or_capture with full detail.
            failures.append(ImportFailure(doi=doi, reason=_GENERIC_IMPORT_ERROR))
            continue

        paper, content = outcome
        filename = _paper_filename(paper)
        document = None
        created = False
        try:
            document, created = await save_uploaded_document(
                user_id=current_user.id, filename=filename, content=content,
                db=db, thread_id=None,
            )
            if created:
                document.doi = paper.doi
                document.source_url = paper.source_url
            # Deliberately NOT stamping doi/source_url on a duplicate: the
            # existing document may belong to a lab mate, and writes stay
            # owner-only (group membership grants read, never modify).
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Failed to store discovered paper %s", doi)
            if document is not None and created:
                # save_uploaded_document created and wrote a NEW file, but the
                # commit above failed -- delete it so it isn't orphaned. A
                # deduplicated document must never be unlinked here: its file
                # backs a pre-existing row (possibly a lab mate's).
                # If save_uploaded_document itself raised, `document` is still
                # None and there is nothing to clean up (that failure mode is
                # documented on save_uploaded_document).
                orphan_path = Path(document.original_path)
                orphan_path.unlink(missing_ok=True)
                logger.info("Cleaned up orphaned PDF for %s: %s", doi, orphan_path)
            failures.append(ImportFailure(doi=doi, reason=_GENERIC_IMPORT_ERROR))
            continue

        if not created:
            # Neither a success nor a failure: the paper is already here and
            # nothing was done. Counting it as "imported" would claim an import
            # that never happened.
            already_in_library.append(doi)
            continue

        background_tasks.add_task(process_document_async, document.id)
        imported += 1

    return ImportResponse(
        imported=imported, failed=failures, already_in_library=already_in_library,
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
    group_id = await get_user_group_id(current_user.id, db)
    results = await combined_search(
        query=q,
        user_id=current_user.id,
        db=db,
        experiment_id=experiment_id,
        doc_limit=doc_limit,
        fov_limit=fov_limit,
        group_id=group_id,
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
    group_id = await get_user_group_id(current_user.id, db)
    results = await search_documents(q, current_user.id, db, limit=limit, group_id=group_id)
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
    # Verify caller may read the document (own or group-shared library doc)
    group_id = await get_user_group_id(current_user.id, db)
    document = await get_document_for_user(db, document_id, current_user.id, group_id)

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
