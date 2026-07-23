"""RAG (Retrieval-Augmented Generation) service for vector search.

This service provides:
- Document page search using vector similarity
- FOV image search using vector similarity
- Combined search across all knowledge sources
- Passage extraction from document pages
"""

import base64
import hashlib
import json
import logging
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Dict, Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from models.rag_document import RAGDocument, RAGDocumentPage, DocumentStatus, document_read_scope, document_scope
from models.image import Image
from models.experiment import Experiment

logger = logging.getLogger(__name__)
settings = get_settings()

# Directory for cached passage images (stored in rag_document_dir parent)
PASSAGES_CACHE_DIR = "rag_passages"

_IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def image_mime_type(path) -> str:
    """MIME type for a stored page/passage image, derived from its extension.

    Page images are written in whichever format ``settings.rag_page_format``
    selects, and older documents may still be PNG, so the type cannot be
    assumed -- serving WebP bytes labelled image/png makes some clients
    refuse to render them.
    """
    return _IMAGE_MIME_BY_SUFFIX.get(Path(path).suffix.lower(), "image/png")


class RAGServiceError(Exception):
    """Exception raised when RAG service encounters an error."""
    pass


def _owner_clause(group_id: Optional[int]) -> str:
    """Raw-SQL page ACL. SSOT mirror of models.rag_document.document_read_scope:
    library docs are group-shared; attachments (group_id NULL) match only the owner."""
    if group_id is not None:
        return "(rd.user_id = :user_id OR (rd.thread_id IS NULL AND rd.group_id = :group_id))"
    return "rd.user_id = :user_id"


async def _has_indexed_pages(user_id: int, db: AsyncSession, group_id: Optional[int] = None) -> bool:
    """Cheap precheck so we skip the (expensive) encoder load when there is
    nothing in the caller's scope to search."""
    owner_clause = _owner_clause(group_id)
    params = {"user_id": user_id}
    if group_id is not None:
        params["group_id"] = group_id
    result = await db.execute(
        text(
            "SELECT 1 FROM rag_document_pages rdp "
            "JOIN rag_documents rd ON rd.id = rdp.document_id "
            f"WHERE {owner_clause} AND rd.status = 'completed' "
            "AND rdp.embedding IS NOT NULL LIMIT 1"
        ),
        params,
    )
    return result.first() is not None


async def _search_pages_by_embedding(
    embedding_list,
    user_id: int,
    db: AsyncSession,
    *,
    limit: int,
    group_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    document_ids: Optional[List[int]] = None,
    exclude_page_id: Optional[int] = None,
    include_text: bool = True,
) -> List[dict]:
    """The one pgvector cosine-search path, shared by text-query, image-example,
    page-example and text-example search. ``embedding_list`` may be a Python list
    or a pgvector ``::text`` string (an existing page's stored vector)."""
    owner_clause = _owner_clause(group_id)
    params = {"embedding": str(embedding_list), "user_id": user_id, "limit": limit}
    if group_id is not None:
        params["group_id"] = group_id

    # Optional filter: restrict to specific documents. Bound as a parameter
    # (never string-interpolated) and still scoped by owner_clause.
    doc_filter = ""
    if document_ids:
        doc_filter = "AND rdp.document_id = ANY(:document_ids)"
        params["document_ids"] = [int(d) for d in document_ids]

    exclude_filter = ""
    if exclude_page_id is not None:
        exclude_filter = "AND rdp.id <> :exclude_page_id"
        params["exclude_page_id"] = int(exclude_page_id)

    # Thread scoping (SSOT mirror of models.rag_document.document_scope): a
    # conversation sees the library plus its OWN attachments, never another thread's.
    if thread_id is None:
        scope_filter = "AND rd.thread_id IS NULL"
    else:
        scope_filter = "AND (rd.thread_id IS NULL OR rd.thread_id = :thread_id)"
        params["thread_id"] = thread_id

    query_sql = text(f"""
        SELECT
            rdp.id,
            rdp.document_id,
            rdp.page_number,
            rdp.image_path,
            rdp.extracted_text,
            rd.name as document_name,
            rd.file_type,
            rd.page_count as total_pages,
            rdp.embedding <=> :embedding as distance
        FROM rag_document_pages rdp
        JOIN rag_documents rd ON rd.id = rdp.document_id
        WHERE {owner_clause}
          AND rd.status = 'completed'
          AND rdp.embedding IS NOT NULL
          {scope_filter}
          {doc_filter}
          {exclude_filter}
        ORDER BY rdp.embedding <=> :embedding
        LIMIT :limit
    """)

    result = await db.execute(query_sql, params)
    results = []
    for row in result.fetchall():
        item = {
            "page_id": row.id,
            "document_id": row.document_id,
            "document_name": row.document_name,
            "file_type": row.file_type,
            "page_number": row.page_number,
            "total_pages": row.total_pages,
            "page_image_url": f"/api/rag/documents/{row.document_id}/pages/{row.page_number}/image",
            "similarity_score": round(1 - row.distance, 4),
        }
        if include_text and row.extracted_text:
            text_content = row.extracted_text
            if len(text_content) > 2000:
                text_content = text_content[:2000] + "... [truncated]"
            item["extracted_text"] = text_content
        results.append(item)
    return results


async def search_documents(
    query: str,
    user_id: int,
    db: AsyncSession,
    limit: int = None,
    include_text: bool = True,
    document_ids: Optional[List[int]] = None,
    thread_id: Optional[int] = None,
    group_id: Optional[int] = None,
) -> List[dict]:
    """Search uploaded documents by a text query (Qwen VL embeddings, pgvector cosine)."""
    from ml.rag import get_qwen_vl_encoder

    if limit is None:
        limit = settings.rag_max_document_results
    if not await _has_indexed_pages(user_id, db, group_id):
        return []
    try:
        encoder = get_qwen_vl_encoder()
        embedding_list = encoder.encode_query(query).tolist()
        return await _search_pages_by_embedding(
            embedding_list,
            user_id,
            db,
            limit=limit,
            group_id=group_id,
            thread_id=thread_id,
            document_ids=document_ids,
            include_text=include_text,
        )
    except Exception as e:
        logger.exception(f"Error searching documents for query: {query[:50]}...")
        # Raise so callers can distinguish "no results" from "search failed".
        raise RAGServiceError(f"Document search failed: {e}") from e


async def search_similar_pages(
    user_id: int,
    db: AsyncSession,
    *,
    page_id: Optional[int] = None,
    document_id: Optional[int] = None,
    image_id: Optional[int] = None,
    limit: int = 10,
    group_id: Optional[int] = None,
    thread_id: Optional[int] = None,
) -> List[dict]:
    """Query-by-example: find pages similar to an EXISTING indexed page /
    document / FOV image, reusing its stored embedding (no encoder call)."""
    owner_clause = _owner_clause(group_id)
    params = {"user_id": user_id}
    if group_id is not None:
        params["group_id"] = group_id

    exclude_page_id = None
    if page_id is not None:
        params["pid"] = int(page_id)
        row = (await db.execute(text(
            f"SELECT rdp.embedding::text AS emb FROM rag_document_pages rdp "
            f"JOIN rag_documents rd ON rd.id = rdp.document_id "
            f"WHERE rdp.id = :pid AND {owner_clause} AND rdp.embedding IS NOT NULL"
        ), params)).first()
        exclude_page_id = int(page_id)
    elif document_id is not None:
        params["did"] = int(document_id)
        row = (await db.execute(text(
            f"SELECT rdp.embedding::text AS emb FROM rag_document_pages rdp "
            f"JOIN rag_documents rd ON rd.id = rdp.document_id "
            f"WHERE rd.id = :did AND {owner_clause} AND rdp.embedding IS NOT NULL "
            f"ORDER BY rdp.page_number LIMIT 1"
        ), params)).first()
    elif image_id is not None:
        # FOV images are owner-scoped via their experiment (fail-closed).
        row = (await db.execute(text(
            "SELECT i.rag_embedding::text AS emb FROM images i "
            "JOIN experiments e ON e.id = i.experiment_id "
            "WHERE i.id = :iid AND e.user_id = :user_id AND i.rag_embedding IS NOT NULL"
        ), {"iid": int(image_id), "user_id": user_id})).first()
    else:
        raise RAGServiceError("search_similar_pages requires page_id, document_id or image_id")

    if row is None or row.emb is None:
        return []
    return await _search_pages_by_embedding(
        row.emb, user_id, db, limit=limit, group_id=group_id,
        thread_id=thread_id, exclude_page_id=exclude_page_id, include_text=True,
    )


async def search_documents_metadata(
    user_id: int,
    db: AsyncSession,
    *,
    name: Optional[str] = None,
    doi: Optional[str] = None,
    file_type: Optional[str] = None,
    status: Optional[str] = None,
    created_after=None,
    created_before=None,
    min_pages: Optional[int] = None,
    max_pages: Optional[int] = None,
    folder_id: Optional[int] = None,
    in_folder: bool = False,
    group_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 50,
) -> List:
    """Filter documents by metadata (name/doi/type/status/date/page-range).
    Returns RAGDocument ORM rows. (Vision-RAG: no OCR text to full-text search.)"""
    stmt = select(RAGDocument).where(document_scope(user_id, thread_id, group_id))
    if name:
        stmt = stmt.where(RAGDocument.name.ilike(f"%{name}%"))
    if doi:
        stmt = stmt.where(RAGDocument.doi.ilike(f"%{doi}%"))
    if file_type:
        stmt = stmt.where(RAGDocument.file_type == file_type)
    if status:
        stmt = stmt.where(RAGDocument.status == status)
    if created_after is not None:
        stmt = stmt.where(RAGDocument.created_at >= created_after)
    if created_before is not None:
        stmt = stmt.where(RAGDocument.created_at <= created_before)
    if min_pages is not None:
        stmt = stmt.where(RAGDocument.page_count >= min_pages)
    if max_pages is not None:
        stmt = stmt.where(RAGDocument.page_count <= max_pages)
    if in_folder:  # scope to one folder (folder_id=None -> root)
        stmt = stmt.where(
            RAGDocument.folder_id.is_(None) if folder_id is None
            else RAGDocument.folder_id == folder_id
        )
    stmt = stmt.order_by(RAGDocument.created_at.desc()).offset(skip).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def search_fov_images(
    query: str,
    user_id: int,
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    limit: int = None,
) -> List[dict]:
    """
    Search FOV images using vector similarity with Qwen VL embeddings.

    Args:
        query: User's search query text
        user_id: User ID for filtering
        db: Database session
        experiment_id: Optional experiment ID to filter results
        limit: Maximum number of results

    Returns:
        List of search results with image info, URLs for display, and similarity scores
    """
    from ml.rag import get_qwen_vl_encoder

    if limit is None:
        limit = settings.rag_max_fov_results

    # Skip the (expensive) embedding-model load when no FOV images are indexed.
    has_indexed = await db.execute(
        text(
            "SELECT 1 FROM images i JOIN experiments e ON e.id = i.experiment_id "
            "WHERE e.user_id = :user_id AND i.rag_embedding IS NOT NULL LIMIT 1"
        ),
        {"user_id": user_id},
    )
    if has_indexed.first() is None:
        return []

    try:
        # Generate query embedding using Qwen VL encoder
        encoder = get_qwen_vl_encoder()
        query_embedding = encoder.encode_query(query)
        embedding_list = query_embedding.tolist()

        # Build query with optional experiment filter
        # Note: cell_count is computed from cell_crops table, not stored on images
        base_select = """
            SELECT
                i.id,
                i.experiment_id,
                i.original_filename,
                i.width,
                i.height,
                e.name as experiment_name,
                i.rag_embedding <=> :embedding as distance
            FROM images i
            JOIN experiments e ON e.id = i.experiment_id
            WHERE e.user_id = :user_id
              AND i.rag_embedding IS NOT NULL
        """

        if experiment_id:
            query_sql = text(base_select + """
              AND i.experiment_id = :experiment_id
            ORDER BY i.rag_embedding <=> :embedding
            LIMIT :limit
            """)
            params = {
                "embedding": str(embedding_list),
                "user_id": user_id,
                "experiment_id": experiment_id,
                "limit": limit,
            }
        else:
            query_sql = text(base_select + """
            ORDER BY i.rag_embedding <=> :embedding
            LIMIT :limit
            """)
            params = {
                "embedding": str(embedding_list),
                "user_id": user_id,
                "limit": limit,
            }

        result = await db.execute(query_sql, params)
        rows = result.fetchall()

        return [
            {
                "image_id": row.id,
                "experiment_id": row.experiment_id,
                "experiment_name": row.experiment_name,
                "filename": row.original_filename,
                "width": row.width,
                "height": row.height,
                "thumbnail_url": f"/api/images/{row.id}/file?type=thumbnail",
                "mip_url": f"/api/images/{row.id}/file?type=mip",
                "similarity_score": round(1 - row.distance, 4),
            }
            for row in rows
        ]

    except Exception as e:
        logger.exception(f"Error searching FOV images for query: {query[:50]}...")
        # Raise the error instead of silently returning empty results
        raise RAGServiceError(f"FOV image search failed: {e}") from e


async def combined_search(
    query: str,
    user_id: int,
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    doc_limit: int = None,
    fov_limit: int = None,
    group_id: Optional[int] = None,
) -> dict:
    """
    Combined search across documents and FOV images.

    Args:
        query: User's search query text
        user_id: User ID for filtering
        db: Database session
        experiment_id: Optional experiment ID for FOV filtering
        doc_limit: Max document results
        fov_limit: Max FOV results
        group_id: Caller's group, for widening document library search

    Returns:
        Dict with 'documents', 'fov_images' lists, and optional 'errors' if any search failed
    """
    if doc_limit is None:
        doc_limit = settings.rag_max_document_results
    if fov_limit is None:
        fov_limit = settings.rag_max_fov_results

    documents = []
    fov_images = []
    errors = []

    # Try document search, capture errors but don't fail entirely
    try:
        documents = await search_documents(query, user_id, db, limit=doc_limit, group_id=group_id)
    except RAGServiceError as e:
        logger.error(f"Document search failed in combined_search: {e}")
        errors.append(f"Document search: {str(e)}")

    # Try FOV search, capture errors but don't fail entirely
    try:
        fov_images = await search_fov_images(
            query, user_id, db,
            experiment_id=experiment_id,
            limit=fov_limit
        )
    except RAGServiceError as e:
        logger.error(f"FOV image search failed in combined_search: {e}")
        errors.append(f"FOV search: {str(e)}")

    result = {
        "query": query,
        "documents": documents,
        "fov_images": fov_images,
    }

    # Include errors if any occurred (so frontend can show warning)
    if errors:
        result["search_errors"] = errors

    return result


async def get_context_for_chat(
    query: str,
    user_id: int,
    db: AsyncSession,
    max_context_items: int = 10,
) -> str:
    """
    Generate context string for chat LLM from RAG search results.

    Args:
        query: User's query
        user_id: User ID
        db: Database session
        max_context_items: Maximum items to include in context

    Returns:
        Formatted context string for LLM
    """
    results = await combined_search(
        query, user_id, db,
        doc_limit=max_context_items // 2,
        fov_limit=max_context_items // 2
    )

    context_parts = []

    # Add document context
    if results["documents"]:
        context_parts.append("## Relevant Documents\n")
        for doc in results["documents"]:
            context_parts.append(
                f"- **{doc['document_name']}** (page {doc['page_number']}): "
                f"[Score: {doc['similarity_score']:.2f}]"
            )

    # Add FOV image context
    if results["fov_images"]:
        context_parts.append("\n## Relevant Microscopy Images\n")
        for img in results["fov_images"]:
            context_parts.append(
                f"- **{img['filename']}** from experiment '{img['experiment_name']}': "
                f"[Score: {img['similarity_score']:.2f}]"
            )

    if not context_parts:
        return "No relevant documents or images found in the knowledge base."

    return "\n".join(context_parts)


async def index_fov_image(image_id: int, db: AsyncSession) -> bool:
    """
    Index a single FOV image for RAG search.

    Args:
        image_id: Image ID to index
        db: Database session

    Returns:
        True if successful

    Raises:
        RAGServiceError: If indexing fails for any reason
    """
    from ml.rag import get_qwen_vl_encoder
    from sqlalchemy import func
    from PIL import Image as PILImage
    from pathlib import Path

    try:
        result = await db.execute(
            select(Image).where(Image.id == image_id)
        )
        image = result.scalar_one_or_none()
        if not image:
            raise RAGServiceError(f"Image {image_id} not found for RAG indexing")

        # Get image path — prefer MIP (web-friendly PNG) over original TIFF
        raw_path = image.mip_path or image.file_path
        image_path = Path(raw_path)
        if not image_path.exists():
            raise RAGServiceError(f"Image file not found: {image_path}")

        # Generate embedding
        encoder = get_qwen_vl_encoder()
        pil_image = PILImage.open(image_path).convert("RGB")
        embedding = encoder.encode_document(pil_image)

        # Update image record
        image.rag_embedding = embedding.tolist()
        image.rag_indexed_at = func.now()
        await db.commit()

        logger.info(f"Indexed FOV image {image_id} for RAG")
        return True

    except RAGServiceError:
        # Re-raise our own exceptions
        raise
    except Exception as e:
        logger.exception(f"Error indexing FOV image {image_id}")
        raise RAGServiceError(f"Failed to index image {image_id}: {e}") from e


async def get_document_content(
    document_id: int,
    user_id: int,
    db: AsyncSession,
    page_numbers: Optional[List[int]] = None,
    max_pages: int = 10,
    include_images: bool = True,
    group_id: Optional[int] = None,
) -> Optional[dict]:
    """
    Get document content including page images for AI vision reading.

    Args:
        document_id: Document ID to retrieve
        user_id: User ID for ownership verification
        db: Database session
        page_numbers: Optional specific pages to return (1-indexed)
        max_pages: Maximum pages to return if no specific pages requested
        include_images: Whether to include base64 encoded images
        group_id: Caller's group, so group-shared library documents are readable too

    Returns:
        Dict with document info and page content (including images), or None if not found
    """
    import base64
    from pathlib import Path

    # Get document with ownership/group-read check
    result = await db.execute(
        select(RAGDocument)
        .options(selectinload(RAGDocument.pages))
        .where(RAGDocument.id == document_id)
        .where(document_read_scope(user_id, group_id))
    )
    document = result.scalar_one_or_none()
    if not document:
        logger.warning(f"Document {document_id} not found for user {user_id} (group_id={group_id})")
        return None

    # Get pages - either specific ones or first N
    pages = document.pages
    if page_numbers:
        # Filter to specific pages, but still cap the count -- a caller could
        # otherwise request every page of a 200-page PDF, inlining ~1.5k vision
        # tokens each and blowing the context window (and re-billing it every
        # loop iteration).
        wanted = set(page_numbers)
        pages = sorted(
            (p for p in pages if p.page_number in wanted),
            key=lambda p: p.page_number,
        )[:max_pages]
    else:
        # Limit to max_pages
        pages = sorted(pages, key=lambda p: p.page_number)[:max_pages]

    page_data = []
    for page in sorted(pages, key=lambda p: p.page_number):
        page_info = {
            "page_number": page.page_number,
            "extracted_text": page.extracted_text if page.extracted_text else None,
            "image_url": f"/api/rag/documents/{document.id}/pages/{page.page_number}/image",
        }

        # Include base64 image for AI vision reading
        if include_images and page.image_path:
            try:
                image_path = Path(page.image_path)
                if image_path.exists():
                    with open(image_path, "rb") as f:
                        image_bytes = f.read()
                    page_info["image_base64"] = base64.b64encode(image_bytes).decode("utf-8")
                    page_info["image_mime_type"] = image_mime_type(image_path)
            except Exception as e:
                logger.warning(f"Failed to load image for page {page.page_number}: {e}")

        page_data.append(page_info)

    return {
        "id": document.id,
        "name": document.name,
        "file_type": document.file_type,
        "total_pages": document.page_count,
        "status": document.status,
        "pages": page_data,
        "note": f"Showing {len(pages)} of {document.page_count} pages. Use page images to read content." if len(pages) < document.page_count else "Use page images to read content.",
    }


async def get_all_documents_summary(
    user_id: int,
    db: AsyncSession,
    include_first_page_text: bool = True,
    thread_id: Optional[int] = None,
    group_id: Optional[int] = None,
) -> List[dict]:
    """
    Get summary of all documents with optional first page text preview.

    Args:
        user_id: User ID
        db: Database session
        include_first_page_text: Whether to include text from first page
        thread_id: Current chat thread, for attachment scoping (this is a LISTING,
            so it must use document_scope, not the fetch-by-id document_read_scope --
            otherwise the owner's chat attachments from OTHER threads would leak in)
        group_id: Caller's group, so group-shared library documents are included

    Returns:
        List of document summaries
    """
    result = await db.execute(
        select(RAGDocument)
        .options(selectinload(RAGDocument.pages))
        .where(document_scope(user_id, thread_id, group_id))
        .where(RAGDocument.status == "completed")
        .order_by(RAGDocument.created_at.desc())
    )
    documents = result.scalars().all()

    summaries = []
    for doc in documents:
        summary = {
            "id": doc.id,
            "name": doc.name,
            "file_type": doc.file_type,
            "page_count": doc.page_count,
        }

        if include_first_page_text and doc.pages:
            first_page = min(doc.pages, key=lambda p: p.page_number)
            if first_page.extracted_text:
                # Get first 500 chars as preview
                preview = first_page.extracted_text[:500]
                if len(first_page.extracted_text) > 500:
                    preview += "..."
                summary["first_page_preview"] = preview

        summaries.append(summary)

    return summaries


async def batch_index_fov_images(
    experiment_id: int,
    user_id: int,
    db: AsyncSession,
) -> dict:
    """
    Index all unindexed FOV images in an experiment.

    Args:
        experiment_id: Experiment ID
        user_id: User ID for ownership verification
        db: Database session

    Returns:
        Dict with success/failure counts
    """
    # Verify experiment ownership
    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.user_id == user_id
        )
    )
    experiment = result.scalar_one_or_none()
    if not experiment:
        return {"error": "Experiment not found", "indexed": 0, "failed": 0}

    # Get unindexed images
    result = await db.execute(
        select(Image).where(
            Image.experiment_id == experiment_id,
            Image.rag_embedding.is_(None)
        )
    )
    images = result.scalars().all()

    indexed = 0
    failed = 0
    errors = []

    for image in images:
        try:
            await index_fov_image(image.id, db)
            indexed += 1
        except RAGServiceError as e:
            failed += 1
            # Collect first few error messages for debugging
            if len(errors) < 5:
                errors.append(f"Image {image.id}: {str(e)}")

    result = {
        "experiment_id": experiment_id,
        "indexed": indexed,
        "failed": failed,
        "total": len(images),
    }

    # Include error details if any failures occurred
    if errors:
        result["error_samples"] = errors
        if failed > len(errors):
            result["error_samples"].append(f"... and {failed - len(errors)} more errors")

    return result


def _get_passage_hash(document_id: int, page_number: int, bbox: List[int]) -> str:
    """Generate a unique hash for a passage based on its location."""
    key = f"{document_id}_{page_number}_{bbox[0]}_{bbox[1]}_{bbox[2]}_{bbox[3]}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _get_passages_cache_path(user_id: int) -> Path:
    """Get the cache directory path for a user's passages."""
    # Store passages alongside RAG documents (in data/rag_passages/{user_id}/)
    cache_dir = settings.rag_document_dir.parent / PASSAGES_CACHE_DIR / str(user_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


async def _get_document_page_image_path(
    document_id: int,
    page_number: int,
    user_id: int,
    db: AsyncSession,
    group_id: Optional[int] = None,
) -> tuple[Optional["RAGDocument"], Optional[Path]]:
    """Load a document and return the filesystem path to a page image.

    Returns (document, image_path) or (None, None) when the document, page,
    or image file cannot be found.
    """
    result = await db.execute(
        select(RAGDocument)
        .options(selectinload(RAGDocument.pages))
        .where(RAGDocument.id == document_id)
        .where(document_read_scope(user_id, group_id))
    )
    document = result.scalar_one_or_none()
    if not document:
        logger.warning(f"Document {document_id} not found for user {user_id}")
        return None, None

    page = next((p for p in document.pages if p.page_number == page_number), None)
    if not page or not page.image_path:
        logger.warning(f"Page {page_number} not found for document {document_id}")
        return None, None

    image_path = Path(page.image_path)
    if not image_path.exists():
        logger.warning(f"Page image not found: {image_path}")
        return None, None

    return document, image_path


async def extract_passage_image(
    document_id: int,
    page_number: int,
    bbox: List[int],
    user_id: int,
    db: AsyncSession,
    padding: int = 30,
    group_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Extract a cropped region (passage) from a document page.

    Args:
        document_id: Document ID
        page_number: Page number (1-indexed)
        bbox: Bounding box [ymin, xmin, ymax, xmax] normalized 0-1000
        user_id: User ID for ownership verification
        db: Database session
        padding: Pixels of padding to add around the crop
        group_id: Caller's group, so group-shared library documents are reachable too

    Returns:
        Dict with image_base64, image_url, source metadata, or None if failed
    """
    from PIL import Image as PILImage

    # Validate bbox format
    if len(bbox) != 4:
        logger.error(f"Invalid bbox format: {bbox}")
        return None

    ymin, xmin, ymax, xmax = bbox

    # Validate bbox values (0-1000 range)
    if not all(0 <= v <= 1000 for v in bbox):
        logger.error(f"Invalid bbox values (must be 0-1000): {bbox}")
        return None

    # Validate bbox order
    if ymin >= ymax or xmin >= xmax:
        logger.error(f"Invalid bbox dimensions: ymin={ymin}, ymax={ymax}, xmin={xmin}, xmax={xmax}")
        return None

    document, image_path = await _get_document_page_image_path(
        document_id, page_number, user_id, db, group_id=group_id
    )
    if not document or not image_path:
        return None

    try:
        # Load the page image
        with PILImage.open(image_path) as img:
            width, height = img.size

            # Denormalize coordinates (0-1000 -> pixels)
            px_xmin = int(xmin * width / 1000)
            px_xmax = int(xmax * width / 1000)
            px_ymin = int(ymin * height / 1000)
            px_ymax = int(ymax * height / 1000)

            # Add padding
            px_xmin = max(0, px_xmin - padding)
            px_xmax = min(width, px_xmax + padding)
            px_ymin = max(0, px_ymin - padding)
            px_ymax = min(height, px_ymax + padding)

            # Validate crop dimensions (min 50x50)
            crop_width = px_xmax - px_xmin
            crop_height = px_ymax - px_ymin
            if crop_width < 50 or crop_height < 50:
                logger.warning(f"Crop too small: {crop_width}x{crop_height}, skipping")
                return None

            # Skip if crop is >90% of page (just return full page reference)
            if crop_width > width * 0.9 and crop_height > height * 0.9:
                return {
                    "type": "full_page",
                    "document_id": document_id,
                    "page_number": page_number,
                    "document_name": document.name,
                    "image_url": f"/api/rag/documents/{document_id}/pages/{page_number}/image",
                    "message": "The relevant content spans most of the page."
                }

            # Crop the image
            cropped = img.crop((px_xmin, px_ymin, px_xmax, px_ymax))

            # Generate hash for caching
            passage_hash = _get_passage_hash(document_id, page_number, bbox)

            # Save to cache using atomic write (temp file + rename)
            cache_path = _get_passages_cache_path(user_id)
            output_path = cache_path / f"{passage_hash}.png"
            temp_path = cache_path / f"{passage_hash}.tmp"
            cropped.save(temp_path, "PNG", optimize=True)
            temp_path.rename(output_path)  # Atomic on POSIX systems

            # Generate base64 for inline display
            buffer = BytesIO()
            cropped.save(buffer, "PNG", optimize=True)
            image_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            buffer.close()  # Explicit cleanup
            cropped.close()  # Explicit cleanup

            logger.info(f"Extracted passage {passage_hash} from doc {document_id} p.{page_number}")

            return {
                "type": "passage",
                "passage_hash": passage_hash,
                "document_id": document_id,
                "page_number": page_number,
                "document_name": document.name,
                "image_base64": image_base64,
                "image_url": f"/api/rag/documents/{document_id}/passages/{passage_hash}",
                "bbox_pixels": {
                    "x": px_xmin,
                    "y": px_ymin,
                    "w": crop_width,
                    "h": crop_height,
                },
                "bbox_normalized": bbox,
            }

    except Exception as e:
        logger.exception(f"Error extracting passage from doc {document_id} p.{page_number}: {e}")
        return None


async def get_cached_passage(
    document_id: int,
    passage_hash: str,
    user_id: int,
    db: AsyncSession,
    group_id: Optional[int] = None,
) -> Optional[Path]:
    """
    Get a cached passage image file path.

    Args:
        document_id: Document ID for ownership verification
        passage_hash: Hash of the passage
        user_id: User ID
        db: Database session
        group_id: Caller's group, so a group-shared library document's cached
            passage is still readable

    Returns:
        Path to the cached image file, or None if not found/unauthorized
    """
    # Verify user may read the document (owner or group-shared library)
    result = await db.execute(
        select(RAGDocument.id).where(
            RAGDocument.id == document_id
        ).where(document_read_scope(user_id, group_id))
    )
    if not result.scalar_one_or_none():
        logger.warning(f"Document {document_id} not found for user {user_id}")
        return None

    # Check cache
    cache_path = _get_passages_cache_path(user_id)
    passage_path = cache_path / f"{passage_hash}.png"

    # Security: Verify path is within expected cache directory (prevent path traversal)
    if not passage_path.resolve().is_relative_to(cache_path.resolve()):
        logger.warning(f"Path traversal attempt detected: {passage_path}")
        return None

    if passage_path.exists():
        return passage_path

    logger.warning(f"Cached passage not found: {passage_path}")
    return None


async def extract_relevant_passages(
    document_id: int,
    page_number: int,
    query: str,
    user_id: int,
    db: AsyncSession,
    max_passages: int = 3,
    group_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Use Gemini vision to find and extract relevant passages from a page.

    Args:
        document_id: Document ID
        page_number: Page number (1-indexed)
        query: What to look for in the page
        user_id: User ID
        db: Database session
        max_passages: Maximum passages to extract
        group_id: Caller's group, so group-shared library documents are reachable too

    Returns:
        List of extracted passage dicts
    """
    import google.genai as genai
    from google.genai import types

    if not settings.gemini_api_key:
        logger.error("GEMINI_API_KEY not configured for passage extraction")
        return []

    document, image_path = await _get_document_page_image_path(
        document_id, page_number, user_id, db, group_id=group_id
    )
    if not document or not image_path:
        logger.warning(f"Cannot extract passages: doc {document_id} p.{page_number} not found for user {user_id}")
        return []

    # Load image as base64
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    # Prompt for spatial understanding - optimized for complete element extraction
    extraction_prompt = f"""Analyze this document page and find the element matching: "{query}"

Return a JSON array with the bounding box of the COMPLETE element:
{{
    "text": "Brief description (max 200 chars)",
    "box_2d": [ymin, xmin, ymax, xmax],
    "type": "figure" | "table" | "text" | "equation",
    "confidence": 0.0-1.0
}}

CRITICAL RULES FOR BOUNDING BOXES:
1. **FIGURES**: Include the ENTIRE figure - the image/chart/graph AND its caption (e.g., "Figure 1: ...") AND any legend/colorbar. The caption is usually below or above the figure.
2. **TABLES**: Include the ENTIRE table - header row, ALL data rows, AND the caption (e.g., "Table 1: ...").
3. **EQUATIONS**: Include the equation AND its number (e.g., "(1)") if present.
4. **TEXT**: Include the complete paragraph or section, not just a single line.

COORDINATE SYSTEM:
- Values are 0-1000 (0=top/left, 1000=bottom/right)
- ymin = top edge, ymax = bottom edge
- xmin = left edge, xmax = right edge
- Add 20-30 pixel margin around the element to avoid cutting off edges

Return ONLY the JSON array. Return [] if nothing found. Maximum {max_passages} elements."""

    try:
        client = genai.Client(api_key=settings.gemini_api_key)

        response = await client.aio.models.generate_content(
            model=settings.gemini_vision_model,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(inline_data=types.Blob(
                            mime_type=image_mime_type(image_path),
                            data=image_base64
                        )),
                        types.Part(text=extraction_prompt)
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                # Gemini 3.x replaces temperature/top_p/top_k with thinking_level;
                # this call relies on the default.
            )
        )

        # Parse the response
        response_text = response.text.strip()

        # Handle markdown code blocks
        if response_text.startswith("```"):
            # Remove ```json and ``` markers
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        passages_data = json.loads(response_text)

        if not isinstance(passages_data, list):
            logger.warning(f"Unexpected response format for passage extraction: {type(passages_data)}")
            return []

        # Extract each passage
        extracted = []
        for passage_info in passages_data[:max_passages]:
            bbox = passage_info.get("box_2d", [])
            if len(bbox) != 4:
                continue

            # Use larger padding for figures and tables (they have captions)
            passage_type = passage_info.get("type", "text")
            padding = 50 if passage_type in ("figure", "table") else 30

            passage = await extract_passage_image(
                document_id=document_id,
                page_number=page_number,
                bbox=bbox,
                user_id=user_id,
                db=db,
                padding=padding,
                group_id=group_id,
            )

            if passage:
                # Add metadata from Gemini
                passage["extracted_text"] = passage_info.get("text", "")
                passage["passage_type"] = passage_info.get("type", "text")
                passage["confidence"] = passage_info.get("confidence", 0.5)
                extracted.append(passage)

        logger.info(f"Extracted {len(extracted)} passages from doc {document_id} p.{page_number}")
        return extracted

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse passage extraction response: {e}")
        return []
    except ValueError as e:
        logger.warning(f"Gemini returned no usable text for doc {document_id} p.{page_number}: {e}")
        return []
    except Exception as e:
        logger.exception(f"Error in passage extraction: {e}")
        return []
