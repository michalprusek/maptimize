#!/usr/bin/env python3
"""Re-embed every indexed page/image in place with the current encoder.

Needed after an encoder change that alters embedding *values* without changing
the dimension (e.g. the mean-pool -> last-token pooling fix). The rendered page
images on disk do not change, so we simply reload each page image and recompute
its embedding — this works uniformly for PDF, image and text documents and needs
no PDF re-render / poppler.

Runs OUT OF BAND (needs the GPU); do not call at startup. Mid-run the index is a
mix of old/new vectors — self-healing as the batch drains — so run at low traffic.

Usage:
    docker exec maptimize-backend python /app/scripts/reindex_all_embeddings.py
    docker exec maptimize-backend python /app/scripts/reindex_all_embeddings.py --fov --limit 50
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

from PIL import Image
from sqlalchemy import select

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reindex")


async def _reembed_document_pages(limit: int | None) -> tuple[int, int]:
    from database import async_session_maker
    from models.rag_document import RAGDocumentPage
    from ml.rag import get_qwen_vl_encoder

    encoder = get_qwen_vl_encoder()
    done = failed = 0
    async with async_session_maker() as db:
        stmt = select(RAGDocumentPage).order_by(RAGDocumentPage.id)
        if limit:
            stmt = stmt.limit(limit)
        pages = (await db.execute(stmt)).scalars().all()
        logger.info("Re-embedding %d document page(s)...", len(pages))
        for i, page in enumerate(pages, start=1):
            path = Path(page.image_path) if page.image_path else None
            if path is None or not path.exists():
                logger.warning("page %d: image missing (%s) — skipped", page.id, page.image_path)
                failed += 1
                continue
            try:
                image = Image.open(path).convert("RGB")
                page.embedding = encoder.encode_document(image).tolist()
                done += 1
            except Exception as exc:  # keep going; one bad page must not abort the run
                logger.error("page %d: encode failed: %s", page.id, exc)
                failed += 1
            if i % 20 == 0:
                await db.commit()
                logger.info("  ...%d/%d", i, len(pages))
        await db.commit()
    return done, failed


async def _reembed_fov_images(limit: int | None) -> tuple[int, int]:
    from database import async_session_maker
    from models.image import Image as ImageModel
    from ml.rag import get_qwen_vl_encoder

    encoder = get_qwen_vl_encoder()
    done = failed = 0
    async with async_session_maker() as db:
        stmt = select(ImageModel).where(ImageModel.rag_embedding.isnot(None)).order_by(ImageModel.id)
        if limit:
            stmt = stmt.limit(limit)
        images = (await db.execute(stmt)).scalars().all()
        logger.info("Re-embedding %d FOV image(s)...", len(images))
        for i, img in enumerate(images, start=1):
            path = Path(img.file_path) if getattr(img, "file_path", None) else None
            if path is None or not path.exists():
                logger.warning("image %d: file missing — skipped", img.id)
                failed += 1
                continue
            try:
                pil = Image.open(path).convert("RGB")
                img.rag_embedding = encoder.encode_document(pil).tolist()
                done += 1
            except Exception as exc:
                logger.error("image %d: encode failed: %s", img.id, exc)
                failed += 1
            if i % 20 == 0:
                await db.commit()
        await db.commit()
    return done, failed


async def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed all indexed content with the current encoder.")
    parser.add_argument("--fov", action="store_true", help="Also re-embed FOV microscopy images.")
    parser.add_argument("--limit", type=int, default=None, help="Cap rows (for a smoke test).")
    args = parser.parse_args()

    # The GPU model manager is populated by the app's startup, which does not run
    # for a standalone script — register the models before acquiring the encoder.
    from ml.gpu_registry import register_all_models
    register_all_models()

    done, failed = await _reembed_document_pages(args.limit)
    logger.info("Document pages: %d re-embedded, %d failed/skipped.", done, failed)
    if args.fov:
        fdone, ffailed = await _reembed_fov_images(args.limit)
        logger.info("FOV images: %d re-embedded, %d failed/skipped.", fdone, ffailed)
        done += fdone
        failed += ffailed
    logger.info("Done.")
    # Non-zero exit so wrapping automation notices a run that failed everything.
    if failed and not done:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
