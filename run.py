from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import uvicorn
from backend.config import settings
from backend.main import app


if __name__ == "__main__":
    url = f"http://localhost:{settings.port}"
    print(f"=== Agent Meeting Room & Benchmark Hub starting on http://{settings.host}:{settings.port} ===", flush=True)
    print(f"    - Web Dashboard : {url}", flush=True)
    print(f"    - MCP Endpoint  : {url}/mcp", flush=True)

    if "--open-browser" in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=settings.host, port=settings.port, reload=False)
