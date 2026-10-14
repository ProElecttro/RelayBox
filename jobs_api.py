import hashlib
import json
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from redis import Redis
from rq import Queue, Retry
from rq.job import Job
from rq.exceptions import NoSuchJobError

from worker import REDIS_URL, run_probe


app = FastAPI(
    title="PathLens Jobs API",
    description="Background network diagnostics with Redis and RQ",
    version="0.2.0",
)

redis_conn = Redis.from_url(REDIS_URL)
queue = Queue("pathlens", connection=redis_conn)

IDEMPOTENCY_TTL = 86400
IDEMPOTENCY_LOCK_TIMEOUT = 30


class ProbeRequest(BaseModel):
    target: str = Field(min_length=1, max_length=2048)
    timeout: float = Field(default=5.0, gt=0, le=30)
    max_redirects: int = Field(default=5, ge=0, le=10)


def validate_target(target: str) -> str:
    target = target.strip()

    if not target or target.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid target")

    candidate = target if "://" in target else f"https://{target}"
    parsed = urlsplit(candidate)

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(
            status_code=400,
            detail="Provide a hostname or HTTPS URL without credentials",
        )

    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid port")

    if port is not None and not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="Invalid port")

    return target


def request_fingerprint(target, timeout, max_redirects):
    payload = json.dumps(
        {
            "target": target,
            "timeout": timeout,
            "max_redirects": max_redirects,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_status(status):
    return status.value if hasattr(status, "value") else str(status)

def enqueue_probe(target, timeout, max_redirects, job_id=None):
    options = {
        "job_timeout": 150,
        "result_ttl": 86400,
        "failure_ttl": 604800,
        "retry": Retry(max=2, interval=[5, 15]),
    }

    if job_id is not None:
        options["job_id"] = job_id

    return queue.enqueue(
        run_probe,
        target,
        timeout,
        max_redirects,
        **options,
    )

@app.get("/health")
def health():
    try:
        redis_conn.ping()
        return {"status": "ok", "redis": "connected"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Redis unavailable",
        )

@app.post("/probes", status_code=202)
def submit_probe(
    request: ProbeRequest,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
):
    target = validate_target(request.target)

    if idempotency_key is None:
        try:
            job = enqueue_probe(
                target, request.timeout, request.max_redirects
            )
            return {
                "job_id": job.id,
                "status": "queued",
                "duplicate": False,
            }
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Could not submit probe job",
            )

    idempotency_key = idempotency_key.strip()
    if not idempotency_key or len(idempotency_key) > 200:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain 1–200 characters",
        )

    fingerprint = request_fingerprint(
        target, request.timeout, request.max_redirects
    )
    key_hash = hashlib.sha256(
        idempotency_key.encode("utf-8")
    ).hexdigest()

    deterministic_job_id = f"pathlens-{key_hash}"
    redis_key = f"pathlens:idempotency:{key_hash}"
    lock_key = f"pathlens:idempotency-lock:{key_hash}"

    try:
        with redis_conn.lock(
            lock_key, timeout=IDEMPOTENCY_LOCK_TIMEOUT, blocking_timeout=5
        ):
            # 1. Check the saved idempotency record.
            existing = redis_conn.get(redis_key)

            if existing:
                data = json.loads(existing)

                if data["fingerprint"] != fingerprint:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Idempotency-Key was already used "
                            "for a different request"
                        ),
                    )

                try:
                    job = Job.fetch(
                        data["job_id"], connection=redis_conn
                    )
                    return {
                        "job_id": job.id,
                        "status": normalize_status(
                            job.get_status(refresh=True)
                        ),
                        "duplicate": True,
                    }
                except NoSuchJobError:
                    redis_conn.delete(redis_key)

            # 2. Recover a job created before its record was saved.
            existing_job = queue.fetch_job(deterministic_job_id)

            if existing_job is not None:
                expected_args = (
                    target,
                    request.timeout,
                    request.max_redirects,
                )

                if tuple(existing_job.args or ()) != expected_args:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Idempotency-Key was already used "
                            "for a different request"
                        ),
                    )

                redis_conn.set(
                    redis_key,
                    json.dumps({
                        "fingerprint": fingerprint,
                        "job_id": existing_job.id,
                    }),
                    ex=IDEMPOTENCY_TTL,
                )

                return {
                    "job_id": existing_job.id,
                    "status": normalize_status(
                        existing_job.get_status(refresh=True)
                    ),
                    "duplicate": True,
                }

            # 3. Create a deterministic job and save its record.
            job = enqueue_probe(
                target,
                request.timeout,
                request.max_redirects,
                job_id=deterministic_job_id,
            )

            redis_conn.set(
                redis_key,
                json.dumps({
                    "fingerprint": fingerprint,
                    "job_id": job.id,
                }),
                ex=IDEMPOTENCY_TTL,
            )

            return {
                "job_id": job.id,
                "status": "queued",
                "duplicate": False,
            }

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Could not submit probe job",
        )


@app.get("/probes/{job_id}")
def get_probe_job(job_id: str):
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    status = normalize_status(job.get_status(refresh=True))

    response = {
        "job_id": job.id,
        "status": status,
    }

    if status == "finished":
        response["result"] = job.return_value()
    elif status == "failed":
        response["error"] = (
            "Probe job failed; inspect worker logs"
        )

    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
