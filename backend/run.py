#!/usr/bin/env python
"""Run the MAPtimize backend server."""
import sys
from pathlib import Path

# Add backend to path for absolute imports
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[str(backend_dir)],
    )
