# PathLens

**Understand why a website or API is slow, unreachable, or failing.**

PathLens is a network diagnostics and monitoring project that breaks a web request into measurable stages: DNS resolution, TCP connection, TLS handshake, and HTTP response. It records probe results, analyzes historical latency, compares performance across runs, and exposes background probes through a REST API.

The project currently runs locally, with distributed probing and advanced monitoring planned for future development.

## Features

- **Network diagnostics:** Measure DNS, TCP, TLS, and HTTP stages independently.
- **TLS inspection:** Validate certificates and measure handshake latency.
- **HTTP health classification:** Distinguish healthy responses, redirects, client errors, and server errors.
- **Redirect handling:** Follow redirects with configurable limits.
- **IP selection:** Probe a specific IP while preserving the original hostname for TLS verification.
- **Persistent history:** Append timestamped probe reports to JSONL.
- **Historical analytics:** Summarize success rates, HTTP statuses, and average stage timings.
- **Performance comparison:** Compare recent runs against a baseline and flag potential latency regressions.
- **REST API:** Trigger probes and retrieve historical statistics.
- **Background jobs:** Use Redis and RQ to queue probes and execute them in a separate worker.
- **Idempotency:** Reuse an existing job for repeated submissions with the same idempotency key.
- **Automated tests:** Validate key API and analytics behaviors.

## Architecture

```text
                         Client
                           |
                +----------+----------+
                |                     |
           Synchronous API         Jobs API
                |                     |
             probe.py             Redis Queue
                                      |
                                    Worker
                                      |
                                   probe.py
                                      |
                       +--------------+--------------+
                       |                             |
                 Network Probe                JSONL History
                DNS / TCP / TLS / HTTP              |
                                                    |
                                      +-------------+-------------+
                                      |                           |
                                  history.py                  compare.py
```

The synchronous API and background-job API are separate entry points. The worker reuses the existing probe implementation rather than duplicating its network logic.

## Tech stack

- Python 3
- FastAPI
- Redis
- RQ (Redis Queue)
- Docker Compose
- Python `socket` and `ssl` networking APIs
- `unittest` for automated testing

## Getting started

### Prerequisites

- Python 3
- Docker Desktop
- Git

### 1. Clone the repository

```bash
git clone https://github.com/ProElecttro/pathLens.git
cd pathLens
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

### 3. Start Redis

```bash
docker compose up -d redis
docker compose ps
```

The default Compose configuration exposes the container's Redis port on `127.0.0.1:6380`. This avoids conflicts with a Redis instance already using port `6379` on the host.

Verify Redis:

```bash
docker compose exec redis redis-cli ping
```

Expected output:

```text
PONG
```

### 4. Run a network probe

```bash
python3 probe.py https://example.com
```

For additional diagnostic output:

```bash
python3 probe.py https://example.com --verbose
```

Configure the timeout and redirect limit:

```bash
python3 probe.py https://example.com --timeout 5
python3 probe.py https://example.com --max-redirects 5
```

Probe a specific IP address while retaining the hostname for TLS verification:

```bash
python3 probe.py https://example.com --ip 93.184.216.34
```

The IP above is an example; use an appropriate address for your target.

By default, probe reports are printed as JSON and appended to `pathlens_history.jsonl`. Use `--no-history` to disable history persistence for a run.

### 5. Analyze historical results

Summarize the most recent runs:

```bash
python3 history.py --last 10
```

Compare recent runs for potential latency regressions:

```bash
python3 compare.py --last 10
```

Set a custom regression threshold:

```bash
python3 compare.py --last 10 --threshold 25
```

The comparison is a heuristic. Small sample sizes and natural network variability can produce false alarms.

## APIs

PathLens provides two API entry points: a synchronous API and a background-job API.

### Synchronous API

Start the API:

```bash
python3 -m uvicorn api:app --reload
```

Open the interactive documentation:

http://127.0.0.1:8000/docs

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Check API health |
| `POST` | `/probes` | Run a probe and return its report |
| `GET` | `/probes/{run_id}` | Retrieve a historical run |
| `GET` | `/stats` | Retrieve aggregate statistics |

### Background-job API

The background API uses Redis and RQ to execute probes independently of the HTTP request.

#### Start the worker

Open a dedicated terminal:

```bash
cd pathLens
source .venv/bin/activate
export REDIS_URL=redis://127.0.0.1:6380/0
python3 worker.py
```

Keep this terminal running.

#### Start the jobs API

Open another terminal:

```bash
cd pathLens
source .venv/bin/activate
export REDIS_URL=redis://127.0.0.1:6380/0
python3 -m uvicorn jobs_api:app --reload
```

Open the interactive documentation:

http://127.0.0.1:8000/docs

#### Submit a probe

`POST /probes`

Request body:

```json
{
  "target": "https://example.com",
  "timeout": 5,
  "max_redirects": 5
}
```

The API returns HTTP `202 Accepted` with a job ID:

```json
{
  "job_id": "generated-job-id",
  "status": "queued",
  "duplicate": false
}
```

#### Retrieve job status

`GET /probes/{job_id}`

Poll this endpoint to retrieve the job status and, when finished, its probe report.

#### Prevent duplicate submissions

Send an `Idempotency-Key` header with the request:

```text
Idempotency-Key: my-probe-request-001
```

Repeated requests with the same key and parameters reuse the recorded job while the idempotency record remains available. Reusing a key with different parameters returns HTTP `409 Conflict`.

Idempotency is currently a best-effort implementation, not an exactly-once processing guarantee. Crash recovery and stronger atomicity remain areas for improvement.

## Running tests

Run the test suite:

```bash
python3 -m unittest discover -s tests -v
```

## Project structure

```text
pathLens/
├── probe.py                 # Network diagnostic engine
├── history.py               # Historical analytics
├── compare.py               # Performance comparisons
├── api.py                   # Synchronous REST API
├── jobs_api.py              # Background-job REST API
├── worker.py                # Redis/RQ worker
├── docker-compose.yml       # Local Redis service
├── requirements.txt         # Python dependencies
├── tests/
│   ├── test_compare.py
│   └── test_jobs_api.py
├── .gitignore
└── README.md
```

## Current limitations

- Probes execute from the local machine; distributed and multi-region probing is not implemented.
- Historical results use append-only JSONL rather than a queryable database.
- Performance comparisons are heuristic and depend on sample size and network variability.
- The HTTP probe is a lightweight diagnostic implementation, not a full browser or HTTP protocol stack.
- The background-job workflow needs stronger crash recovery, concurrency controls, and production hardening.
- Only HTTPS targets are supported by the current probe implementation.
- The current API and worker setup is intended for local development, not unrestricted public deployment.

## Roadmap

- [ ] Improve worker failure recovery and idempotency guarantees.
- [ ] Add concurrency limits and safer retry policies.
- [ ] Persist structured probe and job results in a database.
- [ ] Add scheduled monitoring and alerting.
- [ ] Build a dashboard for latency trends and failure analysis.
- [ ] Explore distributed probe agents and multi-region comparisons.
- [ ] Add deployment and observability support.
- [ ] Expand automated tests for worker failures, retries, and concurrent requests.

## Contributing

Issues and pull requests are welcome. Start with a reproducible failure, a focused improvement, or a test that captures existing behavior.

## License

No license has been specified yet. Until a license is added to the repository, assume that standard copyright restrictions apply.

