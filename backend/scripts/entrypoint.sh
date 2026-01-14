#!/bin/bash
# Docker entrypoint script for Maptimize backend
# Checks for required model weights and downloads if missing

set -e

WEIGHTS_DIR="/app/weights"
SAM_WEIGHTS="$WEIGHTS_DIR/mobile_sam.pt"
YOLO_WEIGHTS="$WEIGHTS_DIR/best.pt"

# SAM weights URL (from Ultralytics releases)
SAM_URL="https://github.com/ultralytics/assets/releases/download/v8.2.0/mobile_sam.pt"

echo "========================================"
echo "Maptimize Backend Startup"
echo "========================================"

# Ensure weights directory exists
mkdir -p "$WEIGHTS_DIR"

# Check SAM weights
if [ ! -f "$SAM_WEIGHTS" ]; then
    echo "SAM weights not found. Downloading MobileSAM..."
    echo "URL: $SAM_URL"

    # Download with curl (progress bar)
    if curl -L -o "$SAM_WEIGHTS.tmp" "$SAM_URL"; then
        mv "$SAM_WEIGHTS.tmp" "$SAM_WEIGHTS"
        echo "✓ SAM weights downloaded successfully"
    else
        echo "✗ Failed to download SAM weights"
        rm -f "$SAM_WEIGHTS.tmp"
        # Continue anyway - SAM will fail gracefully at runtime
    fi
else
    echo "✓ SAM weights found: $SAM_WEIGHTS"
fi

# Check YOLO weights (these are custom-trained, can't auto-download)
if [ ! -f "$YOLO_WEIGHTS" ]; then
    echo "⚠ YOLO weights not found: $YOLO_WEIGHTS"
    echo "  Cell detection will not work without custom-trained weights."
    echo "  Please copy your trained weights to ./weights/best.pt"
else
    echo "✓ YOLO weights found: $YOLO_WEIGHTS"
fi

# Show weights summary
echo ""
echo "Weights summary:"
ls -lh "$WEIGHTS_DIR"/*.pt 2>/dev/null || echo "  No .pt files found"
echo ""

echo "========================================"
echo "Starting application..."
echo "========================================"

# Execute the main command
exec "$@"
