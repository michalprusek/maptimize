"""Document indexing service for RAG.

This service handles:
- PDF upload and storage
- PDF page rendering to images
- Embedding generation using Qwen VL
- Document conversion (Office docs to PDF)
"""

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime

from PIL import Image
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db_context
from models.rag_document import (
    RAGDocument, RAGDocumentPage, DocumentStatus, document_dedupe_scope,
)
from utils.groups import get_user_group_id

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


async def _resolve_upload_group(
    user_id: int, thread_id: Optional[int], db: AsyncSession
) -> Optional[int]:
    """Group to stamp on / scope by for a new document. Library uploads
    (thread_id IS NULL) belong to the owner's lab group; chat attachments stay
    owner-private (None). Fail-closed to owner-only if the group can't be
    resolved. Mirrors experiment group stamping in routers/experiments.py."""
    if thread_id is not None:
        return None
    try:
        return await get_user_group_id(user_id, db)
    except Exception:
        logger.exception("Failed to resolve group for user %s; uploading as owner-only", user_id)
        return None


async def _find_duplicate_document(
    user_id: int,
    thread_id: Optional[int],
    group_id: Optional[int],
    content_hash: str,
    db: AsyncSession,
) -> Optional[RAGDocument]:
    """SSOT for the dedupe lookup shared by file and text uploads: the existing
    non-FAILED document with this content hash within the dedupe scope, or None.
    FAILED is excluded so a broken document never absorbs the re-upload that is
    the user's only way to fix it."""
    return (await db.execute(
        select(RAGDocument).where(
            RAGDocument.content_hash == content_hash,
            RAGDocument.status != DocumentStatus.FAILED.value,
            document_dedupe_scope(user_id, thread_id, group_id),
        ).limit(1)
    )).scalar_one_or_none()


async def save_uploaded_document(
    user_id: int,
    filename: str,
    content: bytes,
    db: AsyncSession,
    thread_id: Optional[int] = None,
) -> Tuple[RAGDocument, bool]:
    """
    Save an uploaded document and create DB record, unless it is already here.

    This is the single choke point for BOTH the manual upload endpoint and the
    discovery import, so deduplication applies to both by construction.

    Args:
        user_id: Owner user ID
        filename: Original filename
        content: File content bytes
        db: Database session
        thread_id: If set, this is a chat attachment scoped to that thread
            (NULL = a document-library upload)

    Returns:
        ``(document, created)``. When ``created`` is False the content was
        already present in a document the caller can see: nothing was written
        to disk, no row was added, and the returned document is the existing
        one -- callers MUST NOT schedule indexing for it, and must not write to
        it (it may belong to a lab mate; writes stay owner-only).

    Raises:
        ValueError: unsupported file type, or a filename that resolves outside
            the user's directory. Both are raised BEFORE anything is written.

    Note the one unrecoverable failure mode: if the DB flush raises after
    ``original_path.write_bytes`` has run, the file is on disk with no row and
    the caller cannot clean it up, because no document was returned to name it.
    Everything that can fail is deliberately ordered before the write.
    """
    file_type = get_file_type(filename)
    if not file_type:
        raise ValueError(f"Unsupported file type: {filename}")

    # Resolve the group BEFORE touching the filesystem: the dedupe scope needs
    # it, and so does the row we may be about to create.
    group_id = await _resolve_upload_group(user_id, thread_id, db)

    # Deduplicate before anything is written, so a duplicate leaves no trace.
    content_hash = hashlib.sha256(content).hexdigest()
    existing = await _find_duplicate_document(user_id, thread_id, group_id, content_hash, db)
    if existing is not None:
        logger.info(
            "Duplicate upload of %s (sha256 %s...) -> existing document %s",
            filename, content_hash[:12], existing.id,
        )
        return existing, False

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

    # Create DB record (group_id resolved above stamps library uploads for the
    # lab group; chat attachments keep it None).
    document = RAGDocument(
        user_id=user_id,
        thread_id=thread_id,
        group_id=group_id,
        name=filename,
        file_type=file_type,
        original_path=str(original_path),
        status=DocumentStatus.PENDING.value,
        file_size=len(content),
        content_hash=content_hash,
    )
    db.add(document)
    await db.flush()

    logger.info(f"Saved document {document.id}: {filename} ({file_type})")
    return document, True


async def fail_orphaned_indexing(db: AsyncSession) -> int:
    """Mark documents whose indexing died with a previous process as FAILED.

    Indexing runs as a FastAPI BackgroundTask, which does NOT survive the
    process. Every deploy restarts the backend (see CLAUDE.md), so any document
    left PENDING or PROCESSING when that happened is orphaned: nothing will ever
    pick it up, and `process_document_async`'s own error handling never runs
    because the coroutine is gone.

    Safe to run only at startup, and correct there precisely because a fresh
    process has no background tasks of its own yet -- so any row in these states
    is necessarily a leftover, never a live job.

    Why this matters beyond a stale badge: deduplication deliberately excludes
    only FAILED documents, so a stuck PENDING row would swallow every re-upload
    of that file as "already in your library" and the paper could never be
    indexed again. Group-wide library dedupe makes that unfixable for anyone but
    the owner. Ageing the row to FAILED restores the re-upload as a remedy.
    """
    result = await db.execute(
        update(RAGDocument)
        .where(RAGDocument.status.in_(
            [DocumentStatus.PENDING.value, DocumentStatus.PROCESSING.value]))
        .values(
            status=DocumentStatus.FAILED.value,
            error_message="Indexing was interrupted by a server restart. "
                          "Re-upload the file or use reindex to try again.",
        )
    )
    if result.rowcount:
        logger.warning(
            "Marked %s document(s) as FAILED: indexing was interrupted by a restart",
            result.rowcount,
        )
    return result.rowcount or 0


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

            # Chat attachments are indexed inline in the user's chat flow, so cap
            # the page count to bound rasterization + GPU embedding time and disk.
            # (This is NOT the context-window guard -- that is the 10-page cap in
            # get_document_content.) Library uploads are a deliberate, offline
            # import and stay uncapped.
            attachment_cap = _page_cap_for(document)
            page_images = await render_pdf_to_images(pdf_path, max_pages=attachment_cap)
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
            # A capped attachment must never look like a complete document: the
            # agent would answer "your paper doesn't mention X" from a fraction
            # of it, with citations and no trace. Record the real length.
            if attachment_cap and len(page_images) >= attachment_cap:
                total = await _pdf_total_pages(pdf_path)
                if total and total > len(page_images):
                    document.truncated_from_pages = total
                    logger.warning(
                        "Document %s truncated: indexed %d of %d pages",
                        document.id, len(page_images), total,
                    )
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


def _page_cap_for(document: RAGDocument) -> Optional[int]:
    """Page cap for this document. SSOT: chat attachments are capped, library
    uploads are not. Used by both the initial index and reindex, which must not
    disagree (a reindex previously re-rendered a capped attachment in full)."""
    return settings.chat_attachment_max_pages if document.thread_id else None


async def _pdf_total_pages(pdf_path: Path) -> Optional[int]:
    """True page count of a PDF, independent of how many we rendered."""
    try:
        from pdf2image import pdfinfo_from_path
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: pdfinfo_from_path(str(pdf_path)))
        return int(info["Pages"])
    except Exception as e:
        logger.warning(f"Could not read page count for {pdf_path}: {e}")
        return None


async def render_pdf_to_images(
    pdf_path: Path,
    dpi: int = 150,
    max_pages: Optional[int] = None,
) -> Optional[List[Tuple[int, Image.Image]]]:
    """
    Render PDF pages to images using pdf2image.

    Args:
        pdf_path: Path to the PDF file
        dpi: Resolution for rendering
        max_pages: If set, render only the first N pages (chat attachments cap)

    Returns:
        List of (page_number, PIL Image) tuples, or None if rendering failed.
        Empty list indicates valid 0-page PDF (rare but possible).
    """
    try:
        from pdf2image import convert_from_path

        # -f/-l are passed through to pdftoppm, so unrequested pages are never
        # rasterized -- this bounds peak memory, not just the result size.
        kwargs = {"dpi": dpi, "fmt": "png"}
        if max_pages is not None and max_pages > 0:
            kwargs["first_page"] = 1
            kwargs["last_page"] = max_pages

        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        images = await loop.run_in_executor(
            None,
            lambda: convert_from_path(str(pdf_path), **kwargs)
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
    last_error = None

    for idx, (page_num, image) in enumerate(page_images):
        try:
            # Save page image. Scanned/rendered journal pages are photographic
            # content, the worst case for PNG -- WebP q85 is ~5-10x smaller and
            # Gemini reads it identically. The encoder downscales to 1024px
            # anyway, so lossless full-res buys nothing downstream.
            ext = settings.rag_page_format.lower()
            image_path = pages_dir / f"page_{page_num:04d}.{ext}"
            image.save(
                str(image_path),
                settings.rag_page_format,
                quality=settings.rag_page_quality,
                method=4,
            )

            # Vision-RAG: pages are indexed as images (visual embeddings), NOT
            # OCR'd to text. extracted_text stays NULL; search is semantic over
            # the page-image embeddings and the agent reads the page images.
            embedding = encoder.encode_document(image)

            page = RAGDocumentPage(
                document_id=document.id,
                page_number=page_num,
                image_path=str(image_path),
                embedding=embedding.tolist(),
                extracted_text=None,
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
            last_error = f"{type(e).__name__}: {e}"
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
        # Report the actual last failure rather than guessing a single cause --
        # image (WebP) encoding, the encoder, and DB writes all route here.
        document.error_message = (
            f"All {total_pages} pages failed to process. Last error: {last_error}"[:500]
            if last_error else "All pages failed to process."
        )
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


async def index_text_snippet(
    user_id: int,
    title: str,
    text_content: str,
    db: AsyncSession,
    thread_id: Optional[int] = None,
) -> Tuple[RAGDocument, bool]:
    """Save a raw text snippet as a new document (dedup by sha256 of the text),
    mirroring save_uploaded_document's dedup/scoping. Returns (document, created);
    on created=False nothing was written. Schedule process_text_document only
    when created is True."""
    content = text_content.encode("utf-8")
    content_hash = hashlib.sha256(content).hexdigest()

    group_id = await _resolve_upload_group(user_id, thread_id, db)
    existing = await _find_duplicate_document(user_id, thread_id, group_id, content_hash, db)
    if existing is not None:
        return existing, False

    user_rag_dir = settings.rag_document_dir / str(user_id)
    user_rag_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_path = user_rag_dir / f"{timestamp}_text_{content_hash[:8]}.txt"
    original_path.write_bytes(content)

    document = RAGDocument(
        user_id=user_id,
        thread_id=thread_id,
        group_id=group_id,
        name=(title or "Text snippet")[:255],
        file_type="text",
        original_path=str(original_path),
        status=DocumentStatus.PENDING.value,
        file_size=len(content),
        content_hash=content_hash,
    )
    db.add(document)
    await db.flush()
    logger.info("Saved text document %s: %s", document.id, document.name)
    return document, True


def _render_text_card(text_content: str) -> Image.Image:
    """Render a text snippet to a page-card image, so a text document satisfies
    the same invariants as any other page (viewable, vision-readable) — every
    RAGDocumentPage needs an image_path."""
    from PIL import ImageDraw, ImageFont

    width, margin, line_h, max_chars = 1024, 48, 18, 100
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    lines: list[str] = []
    for para in (text_content.splitlines() or [""]):
        while len(para) > max_chars:
            cut = para.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            lines.append(para[:cut])
            para = para[cut:].lstrip()
        lines.append(para)

    height = min(max(256, margin * 2 + line_h * len(lines)), 4000)
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    y = margin
    for line in lines:
        if y > height - margin:
            break
        draw.text((margin, y), line, fill="black", font=font)
        y += line_h
    return img


async def process_text_document(document_id: int) -> None:
    """Background task: render the stored text to a page card, embed it via
    encode_text, and write a single completed page."""
    from ml.rag import get_qwen_vl_encoder

    async with get_db_context() as db:
        document = (await db.execute(
            select(RAGDocument).where(RAGDocument.id == document_id)
        )).scalar_one_or_none()
        if document is None:
            return
        document.status = DocumentStatus.PROCESSING.value
        await db.commit()
        try:
            text_content = Path(document.original_path).read_text(encoding="utf-8")
            pages_dir = settings.rag_document_dir / str(document.user_id) / f"doc_{document.id}_pages"
            pages_dir.mkdir(parents=True, exist_ok=True)
            ext = settings.rag_page_format.lower()
            image_path = pages_dir / f"page_0001.{ext}"
            card = _render_text_card(text_content)
            card.save(
                str(image_path), settings.rag_page_format,
                quality=settings.rag_page_quality, method=4,
            )

            encoder = get_qwen_vl_encoder()
            embedding = encoder.encode_text(text_content)
            db.add(RAGDocumentPage(
                document_id=document.id,
                page_number=1,
                image_path=str(image_path),
                embedding=embedding.tolist(),
                extracted_text=text_content,
            ))
            document.page_count = 1
            document.status = DocumentStatus.COMPLETED.value
            document.progress = 1.0
            document.indexed_at = func.now()
            await db.commit()
            logger.info("Text document %s processing completed", document.id)
        except Exception as e:
            logger.exception("Error processing text document %s", document_id)
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
    # Intentionally NOT document_read_scope: writes stay owner-only (group grants read, not mutate).
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
        # Intentionally NOT document_read_scope: writes stay owner-only (group grants read, not mutate).
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
        # Same cap as the initial index -- otherwise reindexing a capped
        # attachment would quietly re-render it in full.
        page_images = await render_pdf_to_images(original_path, max_pages=_page_cap_for(document))
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
