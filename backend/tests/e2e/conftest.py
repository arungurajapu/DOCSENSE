"""Live Uvicorn subprocess management and E2E helpers for DocSense backend QA."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

_UVICORN_RUNNER_SCRIPT = """
import sys
import threading
import time
from pathlib import Path
import uvicorn

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

def _watch_stop() -> None:
    while not stop_file.exists():
        time.sleep(0.05)
    server.should_exit = True

threading.Thread(target=_watch_stop, daemon=True).start()
server.run()
""".strip()


def find_free_port() -> int:
    """Bind an ephemeral TCP socket on 127.0.0.1 and return its assigned port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def assert_utc_iso_timestamp(raw_value: str) -> datetime:
    """Validate ISO-8601 string explicitly carries UTC offset (Z or +00:00)."""
    assert isinstance(raw_value, str) and raw_value.strip(), f"Empty timestamp: {raw_value!r}"
    assert raw_value.endswith("Z") or raw_value.endswith("+00:00"), (
        f"Timestamp must explicitly specify UTC timezone: {raw_value!r}"
    )
    parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == UTC.utcoffset(parsed)
    return parsed


def poll_document_until_terminal(
    client: httpx.Client,
    document_id: str,
    *,
    timeout: float = 30.0,
    interval: float = 0.1,
) -> dict[str, Any]:
    """Poll GET /api/v1/documents/{id} until status reaches 'ready' or 'failed'."""
    deadline = time.monotonic() + timeout
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/documents/{document_id}")
        assert resp.status_code == 200, (
            f"Unexpected status {resp.status_code} polling {document_id}: {resp.text}"
        )
        last_payload = resp.json()
        if last_payload.get("status") in {"ready", "failed"}:
            return last_payload
        time.sleep(interval)
    raise AssertionError(
        f"Timed out waiting for document {document_id} to reach terminal state; last={last_payload}"
    )


@dataclass
class LiveBackendProcess:
    """Managed live Uvicorn --workers 1 subprocess with Windows-safe graceful teardown."""

    process: subprocess.Popen[str]
    port: int
    base_url: str
    work_dir: Path
    db_path: Path
    chroma_dir: Path
    upload_dir: Path
    stop_file: Path
    _output_lines: list[str] = field(default_factory=list)
    _reader_thread: threading.Thread | None = None
    _stopped: bool = False
    _captured_output: str = ""
    _exit_code: int | None = None

    def stop(self, timeout: float = 20.0) -> tuple[int, str]:
        """Gracefully trigger lifespan shutdown via stop flag and collect output."""
        if self._stopped:
            return (self._exit_code if self._exit_code is not None else -1, self._captured_output)

        self._stopped = True
        try:
            self.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.stop_file.write_text("stop", encoding="utf-8")
        except OSError:
            pass

        try:
            self._exit_code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self._exit_code = self.process.wait(timeout=5.0)
        finally:
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=2.0)
            self._captured_output = "".join(self._output_lines)
            try:
                self.stop_file.unlink(missing_ok=True)
            except OSError:
                pass

        return (self._exit_code if self._exit_code is not None else -1, self._captured_output)


class BackendStartupError(RuntimeError):
    """Raised when live Uvicorn subprocess exits or fails readiness checks."""

    def __init__(self, message: str, *, exit_code: int | None, output: str) -> None:
        detail = (
            f"{message}\n--- Uvicorn Output ---\n{output.strip()}"
            if output.strip()
            else message
        )
        super().__init__(detail)
        self.exit_code = exit_code
        self.output = output


@contextmanager
def run_live_backend(
    work_dir: Path,
    *,
    env_overrides: dict[str, str] | None = None,
    port: int | None = None,
    startup_timeout: float = 60.0,
) -> Iterator[LiveBackendProcess]:
    """Spawn a real `uvicorn app.main:app --workers 1` subprocess against ``work_dir``."""
    work_dir.mkdir(parents=True, exist_ok=True)
    db_path = work_dir / "db" / "docsense.db"
    chroma_dir = work_dir / "chroma"
    upload_dir = work_dir / "uploads"
    stop_file = work_dir / f".uvicorn_stop_{uuid.uuid4().hex}.flag"
    chosen_port = port if port is not None else find_free_port()
    base_url = f"http://127.0.0.1:{chosen_port}"

    env = os.environ.copy()
    for key in (
        "DATABASE_URL",
        "CHROMA_PERSIST_DIR",
        "CHROMA_COLLECTION_NAME",
        "UPLOAD_DIR",
        "CORS_ORIGINS",
        "MAX_UPLOAD_SIZE_MB",
        "EMBEDDING_MODEL",
    ):
        env.pop(key, None)

    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "CHROMA_PERSIST_DIR": str(chroma_dir),
            "UPLOAD_DIR": str(upload_dir),
            "CORS_ORIGINS": '["http://localhost:3100","http://127.0.0.1:3100"]',
        }
    )
    if env_overrides:
        env.update(env_overrides)

    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", _UVICORN_RUNNER_SCRIPT, str(chosen_port), str(stop_file)],
        cwd=str(BACKEND_DIR),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    output_lines: list[str] = []

    def _drain_stdout() -> None:
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                output_lines.append(line)
        except ValueError:
            pass

    reader_thread = threading.Thread(target=_drain_stdout, daemon=True)
    reader_thread.start()

    server = LiveBackendProcess(
        process=proc,
        port=chosen_port,
        base_url=base_url,
        work_dir=work_dir,
        db_path=db_path,
        chroma_dir=chroma_dir,
        upload_dir=upload_dir,
        stop_file=stop_file,
        _output_lines=output_lines,
        _reader_thread=reader_thread,
    )

    try:
        deadline = time.monotonic() + startup_timeout
        last_error: str = ""
        with httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(2.0, connect=0.25),
        ) as client:
            while time.monotonic() < deadline:
                exit_code = proc.poll()
                if exit_code is not None:
                    _, output = server.stop(timeout=5.0)
                    raise BackendStartupError(
                        f"Uvicorn exited early with code {exit_code} during startup.",
                        exit_code=exit_code,
                        output=output,
                    )
                try:
                    resp = client.get("/api/v1/health")
                    if resp.status_code in {200, 503}:
                        break
                    last_error = f"HTTP {resp.status_code}: {resp.text}"
                except httpx.HTTPError as exc:
                    last_error = repr(exc)
                time.sleep(0.15)
            else:
                exit_code, output = server.stop(timeout=5.0)
                raise BackendStartupError(
                    f"Uvicorn did not become ready within {startup_timeout}s ({last_error}).",
                    exit_code=exit_code,
                    output=output,
                )

        yield server
    finally:
        server.stop()
