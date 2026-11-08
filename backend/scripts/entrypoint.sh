#!/bin/bash
# Docker entrypoint script for Maptimize backend
# Checks that the custom-trained detector weights are mounted

set -eu

readonly WEIGHTS_DIR="/app/weights"
readonly YOLO_WEIGHTS="$WEIGHTS_DIR/best.pt"

echo "========================================"
echo "Maptimize Backend Startup"
echo "========================================"

mkdir -p "$WEIGHTS_DIR"

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
