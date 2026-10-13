import json
import subprocess
import sys
from pathlib import Path
from statistics import mean
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
PROBE_SCRIPT = BASE_DIR / "probe.py"
HISTORY_FILE = BASE_DIR / "pathlens_history.jsonl"

app = FastAPI(
    title="PathLens API",
    description="Network diagnostics and historical performance analysis",
    version="0.1.0",
)


class ProbeRequest(BaseModel):
    target: str = Field(min_length=1, max_length=2048)
    timeout: float = Field(default=5.0, gt=0, le=30)
    max_redirects: int = Field(default=5, ge=0, le=10)


def read_history():
    if not HISTORY_FILE.exists():
        return []

    records = []
    with HISTORY_FILE.open(encoding="utf-8") as file:
        for line in file:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def validate_target(target):
    target = target.strip()

    if not target or target.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid target")

    candidate = target if "://" in target else f"https://{target}"
    parsed = urlsplit(candidate)

    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(
            status_code=400,
            detail="Target must be a hostname or HTTPS URL",
        )

    try:
        parsed.port
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid port")

    return target


@app.get("/health")
def health():
    return {"status": "ok", "service": "PathLens"}


@app.post("/probes", status_code=201)
def create_probe(request: ProbeRequest):
    target = validate_target(request.target)

    command = [
        sys.executable,
        str(PROBE_SCRIPT),
        "--timeout", str(request.timeout),
        "--max-redirects", str(request.max_redirects),
        "--history", str(HISTORY_FILE),
        target,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=BASE_DIR,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Probe timed out")

    if result.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"Probe process failed: {result.stderr[-1000:]}",
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="Probe returned invalid JSON",
        )


@app.get("/probes/{run_id}")
def get_probe(run_id: str):
    for record in reversed(read_history()):
        if record.get("run_id") == run_id:
            return record

    raise HTTPException(status_code=404, detail="Probe run not found")


@app.get("/stats")
def get_stats():
    records = read_history()

    stage_values = {}
    successes = 0

    for record in records:
        if record.get("overall_outcome") == "SUCCESS":
            successes += 1

        for hop in record.get("hops", []):
            for result in hop.get("results", []):
                stage = result.get("stage")
                elapsed = result.get("elapsed_ms")

                if stage and isinstance(elapsed, (int, float)):
                    stage_values.setdefault(stage, []).append(elapsed)

    return {
        "total_runs": len(records),
        "successful_runs": successes,
        "success_rate_percent": (
            round(successes / len(records) * 100, 2)
            if records else 0
        ),
        "average_stage_ms": {
            stage: round(mean(values), 2)
            for stage, values in stage_values.items()
        },
    }
