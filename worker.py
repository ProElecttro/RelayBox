import json
import os
import subprocess
import sys
from pathlib import Path

from redis import Redis
from rq import Queue, Worker

BASE_DIR = Path(__file__).resolve().parent
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

redis_conn = Redis.from_url(REDIS_URL)
queue = Queue("pathlens", connection=redis_conn)


def run_probe(target, timeout=5.0, max_redirects=5):
    history_file = BASE_DIR / "pathlens_history.jsonl"

    command = [
        sys.executable,
        str(BASE_DIR / "probe.py"),
        "--timeout", str(timeout),
        "--max-redirects", str(max_redirects),
        "--history", str(history_file),
        target,
    ]

    result = subprocess.run(
        command,
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] or "Probe failed")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Probe returned invalid JSON") from error


if __name__ == "__main__":
    worker = Worker([queue], connection=redis_conn)
    worker.work()
