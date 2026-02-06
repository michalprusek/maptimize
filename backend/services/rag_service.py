"""RAG (Retrieval-Augmented Generation) service for vector search.

This service provides:
- Document page search using vector similarity
- FOV image search using vector similarity
- Combined search across all knowledge sources
- Passage extraction from document pages
"""

import base64
import hashlib
import logging
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Dict, Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from models.rag_document import RAGDocument, RAGDocumentPage, DocumentStatus
from models.image import Image
from models.experiment import Experiment

logger = logging.getLogger(__name__)
settings = get_settings()

# Directory for cached passage images (stored in rag_document_dir parent)
PASSAGES_CACHE_DIR = "rag_passages"


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
) -> tuple[Optional["RAGDocument"], Optional[Path]]:
    """Load a document and return the filesystem path to a page image.

    Returns (document, image_path) or (None, None) when the document, page,
    or image file cannot be found.
    """
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

    document, image_path = await _get_document_page_image_path(document_id, page_number, user_id, db)
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
) -> Optional[Path]:
    """
    Get a cached passage image file path.

    Args:
        document_id: Document ID for ownership verification
        passage_hash: Hash of the passage
        user_id: User ID
        db: Database session

    Returns:
        Path to the cached image file, or None if not found/unauthorized
    """
    # Verify user owns the document
    result = await db.execute(
        select(RAGDocument.id).where(
            RAGDocument.id == document_id,
            RAGDocument.user_id == user_id
        )
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

    Returns:
        List of extracted passage dicts
    """
    import google.genai as genai
    from google.genai import types

    if not settings.gemini_api_key:
        logger.error("GEMINI_API_KEY not configured for passage extraction")
        return []

    document, image_path = await _get_document_page_image_path(document_id, page_number, user_id, db)
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
            model="gemini-2.0-flash",
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(inline_data=types.Blob(
                            mime_type="image/png",
                            data=image_base64
                        )),
                        types.Part(text=extraction_prompt)
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,  # Low temperature for precise extraction
            )
        )

        # Parse the response
        response_text = response.text.strip()

        # Handle markdown code blocks
        if response_text.startswith("```"):
            # Remove ```json and ``` markers
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        import json
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
