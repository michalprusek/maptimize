"""Feature extraction service for cell crops."""

import asyncio
import logging
from pathlib import Path
from typing import List, Optional
import numpy as np
from PIL import Image as PILImage
import torch
from torchvision import transforms

from .dinov2_encoder import DINOv2Encoder

logger = logging.getLogger(__name__)

# Processing configuration
BATCH_SIZE = 4  # Conservative for large model memory usage
TARGET_SIZE = 224  # ViT input size

# Global encoder instance (lazy loaded singleton)
_encoder: Optional[DINOv2Encoder] = None


def get_encoder() -> DINOv2Encoder:
    """Get or create the global DINOv2 encoder instance."""
    global _encoder
    if _encoder is None:
        logger.info("Initializing DINOv2 encoder (first use)...")
        _encoder = DINOv2Encoder(pooling="cls")
        _encoder.load_model()
    return _encoder


class FeatureExtractor:
    """Extract DINOv2 features from cell crop images."""

    def __init__(self):
        """Initialize the feature extractor with preprocessing pipeline."""
        self.transform = transforms.Compose([
            transforms.Resize((TARGET_SIZE, TARGET_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],  # ImageNet normalization
                std=[0.229, 0.224, 0.225]
            ),
        ])

    def preprocess_image(self, image_path: str) -> Optional[torch.Tensor]:
        """
        Load and preprocess a cell crop image.

        Args:
            image_path: Path to the crop image file.

        Returns:
            Preprocessed tensor (3, 224, 224) or None if failed.
        """
        try:
            path = Path(image_path)
            if not path.exists():
                logger.warning(f"Image not found: {image_path}")
                return None

            img = PILImage.open(path)

            # Convert to RGB (handle grayscale microscopy images)
            if img.mode != 'RGB':
                img = img.convert('RGB')

            return self.transform(img)

        except IOError as e:
            logger.error(f"Failed to read image {image_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to preprocess {image_path}: {e}")
            return None

    def extract_batch(self, image_paths: List[str]) -> List[Optional[np.ndarray]]:
        """
        Extract features for a batch of images.

        Args:
            image_paths: List of paths to crop images.

        Returns:
            List of embedding arrays (or None for failed images).
        """
        encoder = get_encoder()

        # Preprocess images
        tensors = []
        valid_indices = []

        for i, path in enumerate(image_paths):
            tensor = self.preprocess_image(path)
            if tensor is not None:
                tensors.append(tensor)
                valid_indices.append(i)

        if not tensors:
            return [None] * len(image_paths)

        # Stack and extract features
        batch = torch.stack(tensors)
        features = encoder.extract_features(batch)
        features_np = features.numpy()

        # Map back to original order (None for failed)
        results: List[Optional[np.ndarray]] = [None] * len(image_paths)
        for idx, feat_idx in enumerate(valid_indices):
            results[feat_idx] = features_np[idx]

        return results

    async def extract_batch_async(
        self, image_paths: List[str]
    ) -> List[Optional[np.ndarray]]:
        """
        Async wrapper for batch extraction.

        Runs feature extraction in thread pool to avoid blocking.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.extract_batch(image_paths)
        )


async def extract_features_for_crops(crop_ids: List[int], db) -> dict:
    """
    Extract DINOv2 features for cell crops and update database.

    Args:
        crop_ids: List of CellCrop IDs to process.
        db: AsyncSession database connection.

    Returns:
        Dict with success/failed counts.
    """
    from sqlalchemy import select
    from models.cell_crop import CellCrop

    extractor = FeatureExtractor()
    results = {"success": 0, "failed": 0, "total": len(crop_ids)}

    if not crop_ids:
        return results

    # Fetch crops from database
    result = await db.execute(
        select(CellCrop).where(CellCrop.id.in_(crop_ids))
    )
    crops = result.scalars().all()

    if not crops:
        logger.warning(f"No crops found for IDs: {crop_ids}")
        return results

    logger.info(f"Extracting features for {len(crops)} crops...")

    # Process in batches
    for i in range(0, len(crops), BATCH_SIZE):
        batch_crops = crops[i:i + BATCH_SIZE]

        # Filter crops with valid paths and track which ones have paths
        crops_with_paths = []
        paths = []
        for crop in batch_crops:
            if crop.mip_path:
                crops_with_paths.append(crop)
                paths.append(crop.mip_path)
            else:
                logger.warning(f"Crop {crop.id} has no mip_path, skipping")
                results["failed"] += 1

        if not paths:
            continue

        try:
            embeddings = await extractor.extract_batch_async(paths)

            # Match embeddings to crops that had valid paths
            for crop, embedding in zip(crops_with_paths, embeddings):
                if embedding is not None:
                    crop.embedding = embedding.tolist()
                    crop.embedding_model = "dinov2-large"
                    results["success"] += 1
                else:
                    results["failed"] += 1

        except RuntimeError as e:
            logger.error(f"Batch extraction failed (runtime): {e}")
            results["failed"] += len(crops_with_paths)
        except Exception as e:
            logger.exception(f"Batch extraction failed unexpectedly: {e}")
            results["failed"] += len(crops_with_paths)

    # Commit partial results - we want to save successfully processed crops
    # even if some fail
    await db.commit()

    if results["failed"] > 0:
        logger.warning(
            f"Feature extraction completed with {results['failed']} failures "
            f"out of {results['total']} crops"
        )
    else:
        logger.info(
            f"Feature extraction complete: {results['success']} success"
        )

    return results
