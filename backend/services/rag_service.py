"""RAG (Retrieval-Augmented Generation) service for vector search.

This service provides:
- Document page search using vector similarity
- FOV image search using vector similarity
- Combined search across all knowledge sources
"""

import logging
from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from models.rag_document import RAGDocument, RAGDocumentPage, DocumentStatus
from models.image import Image
from models.experiment import Experiment

logger = logging.getLogger(__name__)
settings = get_settings()


class RAGServiceError(Exception):
    """Exception raised when RAG service encounters an error."""
    pass


async def search_documents(
    query: str,
    user_id: int,
    db: AsyncSession,
    limit: int = None,
    include_text: bool = True,
) -> List[dict]:
    """
    Search uploaded documents using vector similarity with Qwen VL embeddings.

    Args:
        query: User's search query text
        user_id: User ID for filtering documents
        db: Database session
        limit: Maximum number of results
        include_text: Whether to include extracted text content

    Returns:
        List of search results with document info, extracted text, and similarity scores
    """
    from ml.rag import get_qwen_vl_encoder

    if limit is None:
        limit = settings.rag_max_document_results

    try:
        # Generate query embedding using Qwen VL encoder
        encoder = get_qwen_vl_encoder()
        query_embedding = encoder.encode_query(query)
        embedding_list = query_embedding.tolist()

        # Vector similarity search using pgvector cosine distance
        # Lower distance = more similar
        query_sql = text("""
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
            WHERE rd.user_id = :user_id
              AND rd.status = 'completed'
              AND rdp.embedding IS NOT NULL
            ORDER BY rdp.embedding <=> :embedding
            LIMIT :limit
        """)

        result = await db.execute(
            query_sql,
            {
                "embedding": str(embedding_list),
                "user_id": user_id,
                "limit": limit,
            }
        )
        rows = result.fetchall()

        results = []
        for row in rows:
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
                # Include text content (truncate if very long for API response)
                text_content = row.extracted_text
                if len(text_content) > 2000:
                    text_content = text_content[:2000] + "... [truncated]"
                item["extracted_text"] = text_content
            results.append(item)

        return results

    except Exception as e:
        logger.exception(f"Error searching documents for query: {query[:50]}...")
        # Raise the error instead of silently returning empty results
        # This allows callers to distinguish "no results" from "search failed"
        raise RAGServiceError(f"Document search failed: {e}") from e


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
        documents = await search_documents(query, user_id, db, limit=doc_limit)
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

        # Get image path
        image_path = Path(settings.upload_dir) / image.file_path
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
    max_pages: int = 5,
    include_images: bool = True,
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

    Returns:
        Dict with document info and page content (including images), or None if not found
    """
    import base64
    from pathlib import Path

    # Get document with ownership check
    result = await db.execute(
        select(RAGDocument)
        .options(selectinload(RAGDocument.pages))
        .where(
            RAGDocument.id == document_id,
            RAGDocument.user_id == user_id
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        return None

    # Get pages - either specific ones or first N
    pages = document.pages
    if page_numbers:
        # Filter to specific pages
        pages = [p for p in pages if p.page_number in page_numbers]
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
                    page_info["image_mime_type"] = "image/png"
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
) -> List[dict]:
    """
    Get summary of all documents with optional first page text preview.

    Args:
        user_id: User ID
        db: Database session
        include_first_page_text: Whether to include text from first page

    Returns:
        List of document summaries
    """
    result = await db.execute(
        select(RAGDocument)
        .options(selectinload(RAGDocument.pages))
        .where(
            RAGDocument.user_id == user_id,
            RAGDocument.status == "completed"
        )
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
