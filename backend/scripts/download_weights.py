#!/usr/bin/env python3
"""
Download model weights for Maptimize.

This script downloads the required model weights:
- MobileSAM for interactive segmentation

Note: YOLO weights are custom-trained and require manual setup.
      They cannot be auto-downloaded.

Usage:
    python scripts/download_weights.py [--force]

Options:
    --force: Re-download even if weights exist
"""

import os
import sys
import argparse
import hashlib
from pathlib import Path
from urllib.request import urlretrieve
from urllib.error import URLError

# Add backend to path for imports
BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

WEIGHTS_DIR = BACKEND_DIR / "weights"

# Model configurations
MODELS = {
    "mobile_sam.pt": {
        "url": "https://github.com/ultralytics/assets/releases/download/v8.2.0/mobile_sam.pt",
        "description": "MobileSAM for interactive segmentation (~40MB)",
        "required": True,
    },
    # YOLO weights are custom-trained, not downloadable
    # "best.pt": {
    #     "url": None,
    #     "description": "Custom YOLO for cell detection",
    #     "required": True,
    # },
}


def get_file_hash(filepath: Path, algorithm: str = "md5") -> str:
    """
    Calculate file hash for integrity verification.

    TODO: Use this for verifying downloaded weights against known checksums.

    Args:
        filepath: Path to file
        algorithm: Hash algorithm (default: md5)

    Returns:
        Hex digest of file hash
    """
    hasher = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def download_with_progress(url: str, dest: Path) -> bool:
    """Download file with progress indicator."""

    def progress_hook(count, block_size, total_size):
        if total_size > 0:
            percent = min(100, count * block_size * 100 // total_size)
            mb_downloaded = count * block_size / 1024 / 1024
            mb_total = total_size / 1024 / 1024
            print(f"\r  Downloading: {percent}% ({mb_downloaded:.1f}/{mb_total:.1f} MB)", end="", flush=True)
        else:
            mb_downloaded = count * block_size / 1024 / 1024
            print(f"\r  Downloading: {mb_downloaded:.1f} MB", end="", flush=True)

    try:
        urlretrieve(url, dest, reporthook=progress_hook)
        print()  # New line after progress
        return True
    except URLError as e:
        print(f"\n  Network error: {e}")
        return False
    except (OSError, IOError) as e:
        print(f"\n  File system error: {e}")
        return False
    except ValueError as e:
        print(f"\n  Invalid URL or data: {e}")
        return False


def download_model(name: str, config: dict, force: bool = False) -> bool:
    """Download a single model."""
    dest = WEIGHTS_DIR / name

    print(f"\n{config['description']}")
    print(f"  Target: {dest}")

    # Check if exists
    if dest.exists() and not force:
        size_mb = dest.stat().st_size / 1024 / 1024
        print(f"  Status: Already exists ({size_mb:.1f} MB)")
        return True

    if not config.get("url"):
        print(f"  Status: No download URL (manual setup required)")
        return not config.get("required", False)

    # Download
    print(f"  Source: {config['url']}")

    # Create temp file
    temp_dest = dest.with_suffix(".tmp")

    if download_with_progress(config["url"], temp_dest):
        # Move to final location
        temp_dest.rename(dest)
        size_mb = dest.stat().st_size / 1024 / 1024
        print(f"  Status: Downloaded ({size_mb:.1f} MB)")
        return True
    else:
        # Cleanup temp file
        if temp_dest.exists():
            temp_dest.unlink()
        print(f"  Status: FAILED")
        return False


def verify_weights() -> bool:
    """Verify all required weights are present."""
    all_ok = True

    print("\nVerifying weights:")

    for name, config in MODELS.items():
        path = WEIGHTS_DIR / name
        if path.exists():
            size_mb = path.stat().st_size / 1024 / 1024
            print(f"  ✓ {name} ({size_mb:.1f} MB)")
        elif config.get("required", False):
            print(f"  ✗ {name} (MISSING - required)")
            all_ok = False
        else:
            print(f"  - {name} (not present - optional)")

    # Check YOLO weights separately (custom trained)
    yolo_path = WEIGHTS_DIR / "best.pt"
    if yolo_path.exists():
        size_mb = yolo_path.stat().st_size / 1024 / 1024
        print(f"  ✓ best.pt (YOLO, {size_mb:.1f} MB)")
    else:
        print(f"  ✗ best.pt (YOLO - MISSING, requires manual setup)")
        all_ok = False

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Download model weights for Maptimize")
    parser.add_argument("--force", action="store_true", help="Re-download existing weights")
    parser.add_argument("--verify", action="store_true", help="Only verify weights, don't download")
    args = parser.parse_args()

    print("=" * 60)
    print("Maptimize Model Weights Downloader")
    print("=" * 60)

    # Create weights directory
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nWeights directory: {WEIGHTS_DIR}")

    if args.verify:
        success = verify_weights()
        sys.exit(0 if success else 1)

    # Download models
    success = True
    for name, config in MODELS.items():
        if not download_model(name, config, force=args.force):
            if config.get("required", False):
                success = False

    # Final verification
    print("\n" + "=" * 60)
    success = verify_weights() and success

    if success:
        print("\n✓ All required weights are ready!")
    else:
        print("\n✗ Some required weights are missing!")
        print("  Check the errors above and try again.")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
