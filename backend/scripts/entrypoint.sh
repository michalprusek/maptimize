#!/bin/bash
# Docker entrypoint script for Maptimize backend
# Checks for required model weights and downloads if missing

set -eu

readonly WEIGHTS_DIR="/app/weights"
readonly SAM_WEIGHTS="$WEIGHTS_DIR/mobile_sam.pt"
readonly SAM_URL="https://github.com/ultralytics/assets/releases/download/v8.2.0/mobile_sam.pt"
readonly YOLO_WEIGHTS="$WEIGHTS_DIR/best.pt"

download_file() {
    local url="$1"
    local dest="$2"
    local name="$3"

    echo "Downloading $name from $url"
    if curl -fL -o "$dest.tmp" "$url"; then
        mv "$dest.tmp" "$dest"
        echo "[OK] $name downloaded successfully"
        return 0
    else
        rm -f "$dest.tmp"
        echo "[FAIL] Failed to download $name"
        return 1
    fi
}

echo "========================================"
echo "Maptimize Backend Startup"
echo "========================================"

mkdir -p "$WEIGHTS_DIR"

if [ -f "$SAM_WEIGHTS" ]; then
    echo "[OK] SAM weights found: $SAM_WEIGHTS"
else
    download_file "$SAM_URL" "$SAM_WEIGHTS" "MobileSAM weights" || true
fi

if [ -f "$YOLO_WEIGHTS" ]; then
    echo "[OK] YOLO weights found: $YOLO_WEIGHTS"
else
    echo "[WARN] YOLO weights not found: $YOLO_WEIGHTS"
    echo "       Cell detection requires custom-trained weights at ./weights/best.pt"
fi

echo ""
echo "Weights summary:"
ls -lh "$WEIGHTS_DIR"/*.pt 2>/dev/null || echo "  No .pt files found"

echo ""
echo "Starting application..."
exec "$@"
