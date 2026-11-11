"""Clean Uvicorn --workers 1 runner for Playwright E2E tests on Windows."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import uvicorn


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: backend_runner.py <port> <stop_file>")

    port = int(sys.argv[1])
    stop_file = Path(sys.argv[2])

    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        workers=1,
        log_level="info",
    )
    server = uvicorn.Server(config)

    def _watch_stop_file() -> None:
        while not stop_file.exists():
            time.sleep(0.05)
        server.should_exit = True

    threading.Thread(target=_watch_stop_file, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
