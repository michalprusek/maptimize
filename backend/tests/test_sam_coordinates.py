#!/usr/bin/env python3
"""Test SAM coordinate transformation.

Run inside Docker:
    docker exec maptimize-backend python tests/test_sam_coordinates.py
"""

import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

# Suppress logging during test
import logging
logging.basicConfig(level=logging.INFO)


def test_sam_coordinate_transformation():
    """Test that SAM segmentation happens at the correct coordinates."""
    from ml.segmentation.sam_encoder import get_sam_encoder
    from ml.segmentation.sam_decoder import get_sam_decoder

    print("Loading SAM model...")
    encoder = get_sam_encoder()
    decoder = get_sam_decoder()

    # Create a test image with known objects
    # Image is 2000x1500 pixels with a distinct circle
    width, height = 2000, 1500
    img = np.zeros((height, width, 3), dtype=np.uint8)

    # Add white circles at known positions (will be segmented)
    # Circle 1: center at (500, 500), radius 100
    for y in range(height):
        for x in range(width):
            if (x - 500) ** 2 + (y - 500) ** 2 <= 100 ** 2:
                img[y, x] = [255, 255, 255]
            if (x - 1500) ** 2 + (y - 1000) ** 2 <= 150 ** 2:
                img[y, x] = [255, 255, 255]

    print(f"Created test image: {width}x{height}")
    print(f"  - Circle 1: center=(500, 500), radius=100")
    print(f"  - Circle 2: center=(1500, 1000), radius=150")

    # Save test image temporarily
    test_img_path = "/tmp/sam_test_image.png"
    Image.fromarray(img).save(test_img_path)

    # Encode image
    print("\nEncoding image...")
    embedding, enc_width, enc_height = encoder.encode_image(test_img_path)
    print(f"Embedding shape: {embedding.shape}")
    print(f"Encoded dimensions: {enc_width}x{enc_height}")

    # Test segmentation at circle 1 center
    print("\n--- Test 1: Click at Circle 1 center (500, 500) ---")
    mask, iou, _ = decoder.predict_mask(
        embedding=embedding,
        image_shape=(height, width),  # (H, W)
        point_coords=[(500, 500)],
        point_labels=[1],
        multimask_output=True,
    )

    # Find mask center of mass
    ys, xs = np.where(mask)
    if len(xs) > 0:
        mask_center_x = int(np.mean(xs))
        mask_center_y = int(np.mean(ys))
        print(f"Click: (500, 500)")
        print(f"Mask center: ({mask_center_x}, {mask_center_y})")
        print(f"IoU: {iou:.3f}")
        print(f"Mask pixels: {np.sum(mask)}")

        # Calculate error
        error = np.sqrt((mask_center_x - 500) ** 2 + (mask_center_y - 500) ** 2)
        print(f"Error from expected center: {error:.1f} pixels")

        if error > 50:
            print("❌ FAIL: Mask center is too far from click position!")
        else:
            print("✓ PASS: Mask center is close to click position")
    else:
        print("❌ FAIL: No mask generated!")

    # Test segmentation at circle 2 center
    print("\n--- Test 2: Click at Circle 2 center (1500, 1000) ---")
    mask2, iou2, _ = decoder.predict_mask(
        embedding=embedding,
        image_shape=(height, width),
        point_coords=[(1500, 1000)],
        point_labels=[1],
        multimask_output=True,
    )

    ys2, xs2 = np.where(mask2)
    if len(xs2) > 0:
        mask_center_x2 = int(np.mean(xs2))
        mask_center_y2 = int(np.mean(ys2))
        print(f"Click: (1500, 1000)")
        print(f"Mask center: ({mask_center_x2}, {mask_center_y2})")
        print(f"IoU: {iou2:.3f}")
        print(f"Mask pixels: {np.sum(mask2)}")

        error2 = np.sqrt((mask_center_x2 - 1500) ** 2 + (mask_center_y2 - 1000) ** 2)
        print(f"Error from expected center: {error2:.1f} pixels")

        if error2 > 50:
            print("❌ FAIL: Mask center is too far from click position!")
        else:
            print("✓ PASS: Mask center is close to click position")
    else:
        print("❌ FAIL: No mask generated!")

    # Clean up
    os.remove(test_img_path)
    print("\n--- Test Complete ---")


if __name__ == "__main__":
    test_sam_coordinate_transformation()
