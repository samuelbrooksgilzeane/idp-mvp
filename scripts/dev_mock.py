import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def terminate(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 5
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> int:
    environment = os.environ.copy()
    environment["IDP_MODE"] = "mock"
    processes = [
        subprocess.Popen(
            [
                "uv",
                "run",
                "--project",
                "backend",
                "uvicorn",
                "idp_app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=ROOT,
            env=environment,
        ),
        subprocess.Popen(
            ["npm", "--prefix", "frontend", "run", "dev"],
            cwd=ROOT,
            env=environment,
        ),
    ]

    stopping = False

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while not stopping:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    return return_code
            time.sleep(0.2)
    finally:
        terminate(processes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
