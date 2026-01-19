"""Document indexing service for RAG.

This service handles:
- PDF upload and storage
- PDF page rendering to images
- Embedding generation using Qwen VL
- Document conversion (Office docs to PDF)
"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime

from PIL import Image
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db_context
from models.rag_document import RAGDocument, RAGDocumentPage, DocumentStatus

logger = logging.getLogger(__name__)
settings = get_settings()


# Supported file types
SUPPORTED_PDF = {".pdf"}
SUPPORTED_OFFICE = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods"}
SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}
SUPPORTED_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".webm"}  # Future enhancement

ALL_SUPPORTED = SUPPORTED_PDF | SUPPORTED_OFFICE | SUPPORTED_IMAGE | SUPPORTED_VIDEO


def get_file_type(filename: str) -> Optional[str]:
    """Determine file type from filename extension."""
    ext = Path(filename).suffix.lower()
    if ext in SUPPORTED_PDF:
        return "pdf"
    elif ext in SUPPORTED_OFFICE:
        return "office"
    elif ext in SUPPORTED_IMAGE:
        return "image"
    elif ext in SUPPORTED_VIDEO:
        return "video"
    return None


def is_supported_file(filename: str) -> bool:
    """Check if file type is supported."""
    ext = Path(filename).suffix.lower()
    return ext in ALL_SUPPORTED


async def save_uploaded_document(
    user_id: int,
    filename: str,
    content: bytes,
    db: AsyncSession,
) -> RAGDocument:
    """
    Save an uploaded document and create DB record.

    Args:
        user_id: Owner user ID
        filename: Original filename
        content: File content bytes
        db: Database session

    Returns:
        Created RAGDocument record
    """
    file_type = get_file_type(filename)
    if not file_type:
        raise ValueError(f"Unsupported file type: {filename}")

    # Create user's RAG document directory
    user_rag_dir = settings.rag_document_dir / str(user_id)
    user_rag_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename with path traversal protection
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Sanitize: only allow alphanumeric, underscore, hyphen (NOT dots to prevent ..)
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in filename)
    # Remove any leading/trailing underscores that could be suspicious
    safe_name = safe_name.strip("_-")
    if not safe_name:
        safe_name = "document"
    # Get extension from original filename and validate
    original_ext = Path(filename).suffix.lower()
    if original_ext and original_ext[1:].isalnum():
        safe_name = f"{safe_name}{original_ext}"
    unique_name = f"{timestamp}_{safe_name}"
    original_path = user_rag_dir / unique_name

    # Final path traversal check: verify the resolved path is still within user directory
    resolved_path = original_path.resolve()
    resolved_user_dir = user_rag_dir.resolve()
    if not str(resolved_path).startswith(str(resolved_user_dir)):
        raise ValueError("Invalid filename: path traversal detected")

    # Save file
    original_path.write_bytes(content)

    # Create DB record
    document = RAGDocument(
        user_id=user_id,
        name=filename,
        file_type=file_type,
        original_path=str(original_path),
        status=DocumentStatus.PENDING.value,
        file_size=len(content),
    )
    db.add(document)
    await db.flush()

    logger.info(f"Saved document {document.id}: {filename} ({file_type})")
    return document


async def process_document_async(document_id: int) -> None:
    """
    Process a document asynchronously (background task).

    This function:
    1. Renders PDF pages to images
    2. Generates embeddings for each page
    3. Updates document status and progress
    """
    async with get_db_context() as db:
        try:
            # Get document
            result = await db.execute(
                select(RAGDocument).where(RAGDocument.id == document_id)
            )
            document = result.scalar_one_or_none()
            if not document:
                logger.error(f"Document {document_id} not found")
                return

            # Update status to processing
            document.status = DocumentStatus.PROCESSING.value
            await db.commit()

            original_path = Path(document.original_path)
            file_type = document.file_type

            # Convert office docs to PDF first
            if file_type == "office":
                pdf_path = await convert_office_to_pdf(original_path)
                if pdf_path is None:
                    document.status = DocumentStatus.FAILED.value
                    document.error_message = "Failed to convert office document to PDF"
                    await db.commit()
                    return
            elif file_type == "pdf":
                pdf_path = original_path
            elif file_type == "image":
                # Handle single image
                await process_single_image(document, original_path, db)
                return
            else:
                document.status = DocumentStatus.FAILED.value
                document.error_message = f"Unsupported file type for processing: {file_type}"
                await db.commit()
                return

            # Render PDF pages to images
            page_images = await render_pdf_to_images(pdf_path)
            if page_images is None:
                document.status = DocumentStatus.FAILED.value
                document.error_message = "Failed to render PDF pages - check if pdf2image and poppler are installed"
                await db.commit()
                return
            if len(page_images) == 0:
                document.status = DocumentStatus.FAILED.value
                document.error_message = "PDF has no pages to index"
                await db.commit()
                return

            document.page_count = len(page_images)
            await db.commit()

            # Generate embeddings for each page
            await process_pdf_pages(document, page_images, db)

        except Exception as e:
            logger.exception(f"Error processing document {document_id}")
            async with get_db_context() as db:
                result = await db.execute(
                    select(RAGDocument).where(RAGDocument.id == document_id)
                )
                document = result.scalar_one_or_none()
                if document:
                    document.status = DocumentStatus.FAILED.value
                    document.error_message = str(e)[:500]
                    await db.commit()


async def convert_office_to_pdf(input_path: Path) -> Optional[Path]:
    """
    Convert Office document to PDF using LibreOffice.

    Uses create_subprocess_exec for safe subprocess execution (no shell injection).

    Args:
        input_path: Path to the office document

    Returns:
        Path to the converted PDF, or None if conversion failed
    """
    try:
        output_dir = input_path.parent
        output_name = input_path.stem + ".pdf"
        output_path = output_dir / output_name

        # Run LibreOffice in headless mode
        # Using create_subprocess_exec (not shell=True) for security
        process = await asyncio.create_subprocess_exec(
            "libreoffice",
            "--headless",
            "--convert-to", "pdf",
            "--outdir", str(output_dir),
            str(input_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)

        if process.returncode != 0:
            logger.error(f"LibreOffice conversion failed: {stderr.decode()}")
            return None

        if not output_path.exists():
            logger.error(f"Converted PDF not found at {output_path}")
            return None

        logger.info(f"Converted {input_path.name} to PDF")
        return output_path

    except asyncio.TimeoutError:
        logger.error(f"LibreOffice conversion timed out for {input_path}")
        return None
    except Exception as e:
        logger.exception(f"Error converting {input_path} to PDF")
        return None


async def render_pdf_to_images(
    pdf_path: Path,
    dpi: int = 150,
) -> Optional[List[Tuple[int, Image.Image]]]:
    """
    Render PDF pages to images using pdf2image.

    Args:
        pdf_path: Path to the PDF file
        dpi: Resolution for rendering

    Returns:
        List of (page_number, PIL Image) tuples, or None if rendering failed.
        Empty list indicates valid 0-page PDF (rare but possible).
    """
    try:
        from pdf2image import convert_from_path

        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        images = await loop.run_in_executor(
            None,
            lambda: convert_from_path(str(pdf_path), dpi=dpi, fmt="png")
        )

        return [(i + 1, img) for i, img in enumerate(images)]

    except ImportError:
        logger.error("pdf2image not installed. Install with: pip install pdf2image")
        return None  # None indicates failure, not empty PDF
    except Exception as e:
        logger.exception(f"Error rendering PDF {pdf_path}: {e}")
        return None  # None indicates failure, not empty PDF


async def process_pdf_pages(
    document: RAGDocument,
    page_images: List[Tuple[int, Image.Image]],
    db: AsyncSession,
) -> None:
    """
    Process PDF pages: save images, extract text, and generate embeddings.

    Args:
        document: RAGDocument record
        page_images: List of (page_number, PIL Image) tuples
        db: Database session
    """
    from ml.rag import get_qwen_vl_encoder

    # Create pages directory
    pages_dir = Path(document.original_path).parent / f"doc_{document.id}_pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    encoder = get_qwen_vl_encoder()
    total_pages = len(page_images)
    successful_pages = 0
    failed_pages = []

    for idx, (page_num, image) in enumerate(page_images):
        try:
            # Save page image
            image_path = pages_dir / f"page_{page_num:04d}.png"
            image.save(str(image_path), "PNG")

            # Extract text using OCR (pytesseract)
            extracted_text = None
            try:
                import pytesseract
                extracted_text = pytesseract.image_to_string(image, lang='eng+ces')
                if extracted_text:
                    extracted_text = extracted_text.strip()
            except Exception as ocr_err:
                logger.warning(f"OCR failed for page {page_num}: {ocr_err}")

            # Generate embedding
            embedding = encoder.encode_document(image)

            # Create page record with extracted text
            page = RAGDocumentPage(
                document_id=document.id,
                page_number=page_num,
                image_path=str(image_path),
                embedding=embedding.tolist(),
                extracted_text=extracted_text,
            )
            db.add(page)

            # Update progress
            document.progress = (idx + 1) / total_pages
            await db.commit()

            successful_pages += 1
            logger.debug(f"Processed page {page_num}/{total_pages} for doc {document.id}")

        except Exception as e:
            logger.error(f"Failed to process page {page_num} of doc {document.id}: {e}")
            failed_pages.append(page_num)
            # Continue with other pages

    # Only mark as completed if at least some pages succeeded
    if successful_pages > 0:
        document.status = DocumentStatus.COMPLETED.value
        document.progress = 1.0
        document.indexed_at = func.now()
        if failed_pages:
            document.error_message = f"Partially indexed. Failed pages: {failed_pages}"
        await db.commit()
        logger.info(f"Document {document.id} processing completed ({successful_pages}/{total_pages} pages)")
    else:
        document.status = DocumentStatus.FAILED.value
        document.error_message = "All pages failed to process. Check Qwen VL encoder."
        await db.commit()
        logger.error(f"Document {document.id} processing failed - no pages indexed")


async def process_single_image(
    document: RAGDocument,
    image_path: Path,
    db: AsyncSession,
) -> None:
    """
    Process a single image document.

    Args:
        document: RAGDocument record
        image_path: Path to the image file
        db: Database session
    """
    from ml.rag import get_qwen_vl_encoder

    try:
        # Load and process image
        image = Image.open(image_path).convert("RGB")
        encoder = get_qwen_vl_encoder()
        embedding = encoder.encode_document(image)

        # Create page record (page 1 for single image)
        page = RAGDocumentPage(
            document_id=document.id,
            page_number=1,
            image_path=str(image_path),
            embedding=embedding.tolist(),
        )
        db.add(page)

        document.page_count = 1
        document.status = DocumentStatus.COMPLETED.value
        document.progress = 1.0
        document.indexed_at = func.now()
        await db.commit()

        logger.info(f"Image document {document.id} processing completed")

    except Exception as e:
        logger.exception(f"Error processing image document {document.id}")
        document.status = DocumentStatus.FAILED.value
        document.error_message = str(e)[:500]
        await db.commit()


async def delete_document(document_id: int, user_id: int, db: AsyncSession) -> bool:
    """
    Delete a RAG document and all associated files.

    Args:
        document_id: Document ID to delete
        user_id: User ID for ownership verification
        db: Database session

    Returns:
        True if deleted, False if not found
    """
    result = await db.execute(
        select(RAGDocument).where(
            RAGDocument.id == document_id,
            RAGDocument.user_id == user_id
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        return False

    # Delete files
    try:
        original_path = Path(document.original_path)
        if original_path.exists():
            original_path.unlink()

        # Delete pages directory
        pages_dir = original_path.parent / f"doc_{document.id}_pages"
        if pages_dir.exists():
            shutil.rmtree(pages_dir)

    except Exception as e:
        logger.warning(f"Error deleting document files: {e}")

    # Delete from DB (cascades to pages)
    await db.delete(document)
    await db.commit()

    logger.info(f"Deleted document {document_id}")
    return True


async def reindex_document(document_id: int, user_id: int) -> dict:
    """
    Re-index an existing document. Useful for fixing failed indexing.

    Args:
        document_id: Document ID to reindex
        user_id: User ID for ownership verification

    Returns:
        Dict with status
    """
    async with get_db_context() as db:
        # Get and verify document
        result = await db.execute(
            select(RAGDocument).where(
                RAGDocument.id == document_id,
                RAGDocument.user_id == user_id
            )
        )
        document = result.scalar_one_or_none()
        if not document:
            return {"error": "Document not found"}

        # Delete existing pages
        from sqlalchemy import delete
        await db.execute(
            delete(RAGDocumentPage).where(RAGDocumentPage.document_id == document_id)
        )

        # Reset document status
        document.status = DocumentStatus.PROCESSING.value
        document.progress = 0.0
        document.error_message = None
        document.indexed_at = None
        await db.commit()

        logger.info(f"Starting reindex of document {document_id}")

        # Re-process the document
        original_path = Path(document.original_path)
        if not original_path.exists():
            document.status = DocumentStatus.FAILED.value
            document.error_message = "Original file not found"
            await db.commit()
            return {"error": "Original file not found"}

        # Render PDF pages
        page_images = await render_pdf_to_images(original_path)
        if page_images is None:
            document.status = DocumentStatus.FAILED.value
            document.error_message = "Failed to render PDF pages - check if pdf2image and poppler are installed"
            await db.commit()
            return {"error": "Failed to render PDF pages - check server logs for details"}
        if len(page_images) == 0:
            document.status = DocumentStatus.FAILED.value
            document.error_message = "PDF has no pages to index"
            await db.commit()
            return {"error": "PDF has no pages to index"}

        document.page_count = len(page_images)
        await db.commit()

        # Process pages
        await process_pdf_pages(document, page_images, db)

        return {
            "status": "completed",
            "document_id": document_id,
            "page_count": document.page_count,
        }


async def get_indexing_status(user_id: int, db: AsyncSession) -> dict:
    """
    Get global indexing status for a user.

    Args:
        user_id: User ID
        db: Database session

    Returns:
        Dict with counts by status
    """
    from models.image import Image

    # Document counts by status
    result = await db.execute(
        select(RAGDocument.status, func.count(RAGDocument.id))
        .where(RAGDocument.user_id == user_id)
        .group_by(RAGDocument.status)
    )
    doc_counts = {row[0]: row[1] for row in result.all()}

    # FOV image counts
    result = await db.execute(
        select(func.count(Image.id))
        .where(Image.rag_embedding.is_(None))
    )
    fov_pending = result.scalar() or 0

    result = await db.execute(
        select(func.count(Image.id))
        .where(Image.rag_embedding.is_not(None))
    )
    fov_indexed = result.scalar() or 0

    return {
        "documents_pending": doc_counts.get("pending", 0),
        "documents_processing": doc_counts.get("processing", 0),
        "documents_completed": doc_counts.get("completed", 0),
        "documents_failed": doc_counts.get("failed", 0),
        "fov_images_pending": fov_pending,
        "fov_images_indexed": fov_indexed,
    }
