# PathLens: Project Context and Development Log

**Last updated:** 11 October 2026\
**Project directory:** `/Users/proelectro/thecode/codeforgood/pathLens`\
**Current stage:** Local Python network-diagnostics CLI prototype\
**Main file:** `probe.py`

## 1. Executive summary

PathLens is being built from scratch to diagnose *where* a website or
API connection fails, rather than merely reporting that a URL is
unavailable. The first version is a command-line probe that checks DNS,
TCP, TLS, and HTTP; records outcomes and timings; follows redirects;
supports pinning a chosen IP; and emits structured JSON.

The broader goal is a real, useful, resume-worthy systems project that
teaches networking and system design through actual behavior. Over time,
it may grow into a distributed network diagnostics and monitoring
platform with scheduled probes, multiple workers/regions, an API,
historical storage, analytics, and alerts. Technologies such as Redis, a
message queue, ClickHouse, reverse proxies, Docker, and cloud deployment
should be introduced only when a concrete requirement justifies them.

## 2. Motivation and problem statement

A website failure can happen at different layers:

-   **DNS:** a hostname cannot be resolved, or the resolver returns
    different addresses in different environments.
-   **TCP:** an address is known, but a connection to its port cannot be
    established.
-   **TLS:** TCP works, but the secure handshake or certificate
    verification fails.
-   **HTTP:** the connection works, but the server returns an error,
    redirect, or unexpected response.
-   **Redirect destination:** the initial host responds, but the next
    host in the chain is unreachable.
-   **Network environment:** VPN state, routing, ISP/firewall behavior,
    DNS caching, or server-side behavior may affect results.

Browsers hide much of this sequence. PathLens aims to make it visible,
with a result and timing for each stage.

**Core problem statement:** Given a hostname or URL, identify which
stages succeeded or failed, record timings and response metadata, and
emit a machine-readable report that can eventually be compared across
runs and locations.

This is intended to be a real system, not a simulation: its initial
measurements come from real DNS lookups, socket connections, TLS
handshakes, and HTTP exchanges.

## 3. Goals

### Near-term

-   Make a reliable local CLI probe.
-   Report DNS, TCP, TLS, and HTTP outcomes separately.
-   Record stage-specific timings and meaningful errors.
-   Keep normal output as valid JSON.
-   Provide optional verbose diagnostic logs.
-   Follow redirects with a maximum-hop limit and loop detection.
-   Allow an IP override for the initial URL while preserving the
    original hostname for TLS and HTTP.
-   Compare runs under different VPN/network conditions without claiming
    causes that have not been proven.
-   Keep the code small and understandable while learning the
    fundamentals.

### Longer-term

-   Probe workers running from multiple machines or regions.
-   API to submit checks and retrieve results.
-   Scheduler for recurring checks.
-   Historical persistence and latency/failure analytics.
-   Alerting for outages and regressions.
-   Dashboard for timings, failures, and redirect chains.
-   Queues, caching, database/analytics storage, reverse proxies, load
    balancing, Docker, and possibly GCP where the system genuinely needs
    them.

These are future directions, not features that should all be added at
once.

## 4. Environment and project boundaries

-   **Directory:** `/Users/proelectro/thecode/codeforgood/pathLens`
-   **Machine:** MacBook Air with Apple Silicon
-   **Python:** `3.14.8`
-   **Docker Compose:** `5.5.1`
-   **Language:** Python
-   **Current interface:** CLI
-   **Current protocol path:** HTTPS over TCP, with a manually
    constructed HTTP/1.1 request

**Important:** PathLens is a separate project. Do not modify the
unrelated `systems-lab` project.

## 5. Current CLI

The latest proposed interface is:

``` text
python3 probe.py [hostname-or-url] [--verbose] [--timeout SECONDS]
                 [--max-redirects COUNT] [--ip IP_ADDRESS]
```

Examples:

``` bash
python3 probe.py example.com
python3 probe.py example.com --verbose
python3 probe.py example.com --timeout 5
python3 probe.py example.com --max-redirects 0
python3 probe.py pornhub.com --ip 66.254.114.41 --verbose
```

A bare hostname is converted to an HTTPS URL,
e.g. `https://example.com/`. The positional target defaults to
`example.com`.

-   `--verbose`: enables progress/debug logs.
-   `--timeout`: network timeout in seconds, default `5`.
-   `--max-redirects`: redirect limit, default `5`.
-   `--ip`: uses the supplied numeric IP for the initial URL's
    connection. It does not automatically pin redirected hosts to the
    same address.

## 6. What has been implemented

### Result helper

The project has used a shared result helper:

``` python
def make_result(stage, outcome, target, elapsed_ms):
    return {
        "stage": stage,
        "outcome": outcome,
        "target": target,
        "elapsed_ms": round(elapsed_ms, 2),
    }
```

Stage-specific metadata can be added, such as HTTP status code, time to
first byte, or redirect location.

### DNS

Normal runs use `socket.getaddrinfo` to resolve the hostname. The
elapsed time and outcome are reported under `DNS_RESOLVE`. The resolver
may return IPv4 and IPv6 addresses, and results can vary with resolver,
network, and time.

When `--ip` is provided, the program selects a numeric address instead
and reports `IP_SELECT`, rather than pretending a DNS lookup occurred. A
pinned-IP run therefore does not measure normal DNS resolution.

### TCP

The probe attempts a connection to the selected IP and port, normally
port `443` for HTTPS. Connection attempts are timed. TCP timeouts are
reported as explicit failures instead of being hidden inside one overall
error. The implementation can try multiple addresses returned by
resolution.

### TLS

After TCP succeeds, the socket is wrapped with Python's `ssl` support.
The original hostname is passed as `server_hostname=hostname`, even when
the destination IP is pinned. This preserves TLS SNI and certificate
hostname verification for the hostname the user requested.

Certificate verification failures and other TLS errors are handled as
separate error cases.

### HTTP

The code sends a basic HTTP/1.1 `GET` request over the TLS socket, with
a `Host` header set to the hostname rather than the IP. It parses the
status line and headers and records metadata such as:

-   HTTP status code
-   Time to first byte (TTFB), based on the current timing boundaries
-   `Location` header when present
-   Stage timings and errors

This is a small learning implementation, not a complete HTTP client.

### Redirects

The implementation recognizes common redirect statuses: `301`, `302`,
`303`, `307`, and `308`. It resolves relative `Location` headers with
`urljoin`, follows destinations up to `--max-redirects`, and tracks
visited URLs to detect loops.

The report is organized as a sequence of `hops`. The `--ip` override is
applied only to the initial URL. A redirect may point to another
hostname, which should normally undergo its own DNS selection and
connection.

### Errors

The code handles important failure cases including resolution failures,
TCP timeouts, TLS certificate verification failures, other TLS errors,
socket/OS errors, unsupported or invalid URLs, redirect loops, and
redirect limits.

## 7. Current report shape

The latest design returns an overall target and a list of hops. A
simplified illustrative example:

``` json
{
  "target": "example.com",
  "overall_outcome": "SUCCESS",
  "redirects_followed": 0,
  "hops": [
    {
      "url": "https://example.com/",
      "outcome": "SUCCESS",
      "status_code": 200,
      "results": [
        {
          "stage": "DNS_RESOLVE",
          "outcome": "SUCCESS",
          "target": "example.com",
          "elapsed_ms": 20.0
        },
        {
          "stage": "TCP_CONNECT",
          "outcome": "SUCCESS",
          "target": "example.com:443",
          "elapsed_ms": 30.0
        }
      ]
    }
  ]
}
```

The numbers above are illustrative, not measured values. Actual reports
contain the results collected during the run.

**Semantics to keep in mind:** in the current design, hop
`outcome: "SUCCESS"` means the probe completed an HTTP exchange and
received a response. It does not guarantee HTTP `200`, application
health, or that a human can use the page successfully. A redirect
response can be a successful hop even if the next hop fails.

## 8. Experiments and observations

### `example.com`

The basic end-to-end probe completed DNS, TCP, TLS, and HTTP and
received HTTP `200`. Timings varied between runs, which is normal for
network measurements. This was the initial sanity check.

### `pornhub.com` with VPN on/off

Observed results differed with VPN state:

-   **VPN off:** one run resolved `pornhub.com` to `13.127.247.216`,
    then TCP connection attempts timed out.
-   **VPN on:** one run resolved it to `66.254.114.41`, and TCP/TLS/HTTP
    completed with HTTP `200`.

This establishes that observed DNS and connectivity differed between
those runs. It does **not** prove the precise cause. Resolver behavior,
routing, filtering, server-side behavior, and transient conditions are
possible factors.

### Pinning `66.254.114.41`

With VPN off:

-   `nc -vz -w 5 66.254.114.41 443` succeeded in opening a TCP
    connection.
-   `curl --resolve pornhub.com:443:66.254.114.41 https://pornhub.com/`
    completed TLS and received a `302` redirect to
    `https://www.pornhub.org/`.
-   Following that redirect caused `www.pornhub.org` to resolve to
    `13.127.247.216`, where TCP timed out in the observed run.

With VPN on, a pinned-IP request returned HTTP `200` and a JavaScript
challenge page.

### What this teaches

Pinning an IP helps separate several variables:

-   **DNS selection:** which IP the hostname resolves to.
-   **TCP reachability:** whether the selected IP and port accept a
    connection.
-   **TLS identity:** whether the server presents a certificate valid
    for the hostname.
-   **HTTP virtual hosting:** which site is requested using the `Host`
    header.
-   **Redirects:** whether the initial response sends the client to
    another hostname.
-   **Network environment:** how the path behaves when VPN state
    changes.

Pinning the initial host does not pin the entire browsing session. If it
redirects to another hostname, that destination normally gets its own
DNS and TCP path.

The report should state measured facts: DNS answers, attempted/connected
IPs, stage outcomes, HTTP status, redirect destination, and timings. It
should not blame a specific ISP, firewall, region, or server policy
without further evidence.

## 9. Concepts PathLens is meant to teach

-   **DNS:** maps hostnames to IP addresses; answers can differ by
    resolver, network, geography, caching, and time.
-   **TCP:** establishes a connection to an address and port. A timeout
    alone does not identify the cause.
-   **TLS:** negotiates encryption and authenticates the server. SNI and
    certificate verification still use the hostname in pinned-IP mode.
-   **HTTP:** carries application requests and responses, including
    status codes, headers, bodies, and redirects.
-   **Redirects:** can change hostname, IP selection, route, and server
    behavior. Each hop is a separate network exchange.
-   **Observability:** consistent stage results and timings enable
    comparisons, aggregation, alerting, and historical analysis.

## 10. Known limitations and technical debt

1.  **Basic HTTP parsing:** the code manually parses the status line and
    headers.
2.  **Response framing:** it reads until connection close and does not
    robustly handle all `Content-Length`, chunked transfer encoding,
    interim responses, or other HTTP framing cases.
3.  **HTTP/2 unsupported:** the request is HTTP/1.1.
4.  **HTTPS-focused:** the current URL probe supports HTTPS; an HTTP
    redirect is not yet handled as a normal hop.
5.  **Outcome semantics:** transport success should eventually be
    distinguished from application-level health.
6.  **Timing definitions:** DNS, TCP connect, TLS handshake, TTFB, and
    total duration need precise documented boundaries.
7.  **Address-level details:** report which resolved address was
    attempted and whether each attempt succeeded or failed.
8.  **Multiple-address reporting:** trying multiple addresses can create
    multiple stage entries; grouping by address would be clearer.
9.  **Per-hop details:** each hop should eventually expose actual
    connected IP, hostname, port, status, and redirect destination.
10. **No persistence/distributed workers yet:** results are local CLI
    output.
11. **Tests:** no formal automated test suite is confirmed in this
    context.
12. **Environment sensitivity:** VPN state, DNS cache, network
    conditions, and server behavior affect measurements.

Improve these based on actual behavior and tests rather than trying to
support everything at once.

## 11. Workflow and Git preferences

-   Explain networking concepts in simple terms and connect them to the
    code or real experiments.
-   Make small, testable changes, while moving efficiently when the next
    step is clear.
-   If asked for a **full file** or **full function**, provide the
    complete code.
-   Verify each meaningful change before adding more complexity.
-   Keep PathLens separate from `systems-lab`.
-   Record meaningful milestones in Git.
-   When revising the latest commit, the preferred workflow is to amend
    it instead of adding a new commit, when appropriate.

Preferred commands after checking the repository state:

``` bash
git status
git add .
git commit --amend --no-edit
git push --force-with-lease origin main
```

To change the commit message:

``` bash
git add .
git commit --amend -m "Meaningful commit message"
git push --force-with-lease origin main
```

Do not run these blindly. Check the current branch, working tree, and
remote state first. Amending rewrites the latest commit, and
force-pushing should only be done when that is the intended workflow.

## 12. Immediate next step: verify the latest full file

The latest assistant response supplied a complete proposed `probe.py`
with redirect following and IP pinning. The context available here does
**not** confirm that this latest complete version has run successfully.
Treat it as the current version to verify, not as a tested release.

From the PathLens directory, run:

``` bash
python3 probe.py example.com
python3 probe.py example.com --verbose
python3 probe.py pornhub.com --ip 66.254.114.41 --verbose
python3 probe.py example.com --max-redirects 0
```

Verify:

-   It runs without syntax/runtime errors.
-   Default output is valid JSON.
-   Verbose logs do not corrupt JSON output if the JSON is meant to be
    piped to tools.
-   Hops appear in redirect order.
-   `--ip` applies only to the initial URL.
-   Redirect loops and redirect limits have clear outcomes.
-   Failures identify the stage that failed.
-   `example.com` reaches an HTTP response in a normal run.

Fix problems found here before adding features.

## 13. Suggested roadmap

### Phase 1: Stabilize the CLI

-   Verify the latest code.
-   Keep JSON output machine-readable.
-   Standardize stage outcomes and error details.
-   Report the actual connected IP.
-   Make redirect loop/limit semantics clear.
-   Separate transport success from application health.
-   Add tests for parsing, redirects, and failures.

### Phase 2: Improve correctness

-   Make HTTP response parsing robust, or use a well-understood HTTP
    library where appropriate while preserving the educational value.
-   Decide how to handle both HTTP and HTTPS redirects.
-   Define precise timing semantics.
-   Report multiple IP attempts clearly.
-   Build local test fixtures for controlled success, failure, and
    redirect cases so tests do not rely only on public websites.

### Phase 3: Make results useful over time

-   Define a stable result schema.
-   Add a small API for submitting probe requests and returning results.
-   Store timestamped runs.
-   Compare latency distributions and failure rates over time.

### Phase 4: Add system-design components when justified

-   Scheduler for recurring checks.
-   Queue when jobs need decoupling or worker-based processing.
-   Redis for a specific caching, rate-limiting, coordination, or
    ephemeral-state need.
-   Database/analytics store based on actual query patterns and data
    volume.
-   Reverse proxy/load balancer for multiple deployed instances.
-   Docker and then cloud deployment after local behavior is reliable.

### Phase 5: Distributed diagnostics

-   Run workers from multiple locations.
-   Compare DNS answers, reachability, TLS, HTTP status, and latency.
-   Detect location-specific failures without overstating their causes.
-   Add alerts and a historical dashboard.

**Guiding principle:** every infrastructure component should solve a
real problem in PathLens. Do not add Redis, Kafka, ClickHouse, or cloud
services merely as decorations.

## 14. Future architecture sketch

``` text
          Dashboard / API client
                   |
                   v
               PathLens API
                   |
             Scheduler / Jobs
                   |
             Queue (if needed)
                   |
       +-----------+-----------+
       |           |           |
       v           v           v
   Worker A    Worker B    Worker C
   Region A    Region B    Region C
       |           |           |
       +---- DNS / TCP / TLS / HTTP ----+
                                        |
                                        v
                              Result storage/analytics
                                        |
                                        v
                              History, comparisons, alerts
```

This is a conceptual future architecture. The current system is a local
CLI probe.

## 15. Glossary

-   **Probe:** a program that performs a diagnostic check against a
    target.
-   **DNS resolution:** translating a hostname into IP addresses.
-   **TCP connect:** attempting to establish a connection to an IP and
    port.
-   **TLS handshake:** negotiating a secure connection and
    authenticating the server.
-   **SNI:** TLS extension through which the client indicates the
    hostname it wants to reach.
-   **HTTP `Host` header:** tells the HTTP server which hostname is
    being requested on a shared IP.
-   **Pinned IP:** explicitly selecting the destination IP for a
    connection.
-   **TTFB:** time from initiating the HTTP request to receiving the
    first response byte, according to the implementation's timing
    boundaries.
-   **Redirect hop:** one URL in a chain of HTTP responses and
    destinations.
-   **Vantage point:** the machine/network location from which a
    measurement is made.
-   **Structured output:** machine-readable data with consistent fields,
    here JSON.

## 16. Short project description

PathLens is a network diagnostics and monitoring tool that decomposes
website/API reachability into DNS resolution, TCP connectivity, TLS
negotiation, and HTTP response stages. Its initial Python CLI records
stage timings and outcomes, supports verbose diagnostics, follows
redirects, and can pin a connection to a chosen IP while retaining the
original hostname for TLS and HTTP. Experiments across VPN states and
redirect destinations demonstrate why a successful initial connection
does not guarantee that the complete request path works. The planned
evolution is toward a distributed monitoring service with historical
results, API access, scheduled probes, and multi-location comparisons,
introducing queues, caching, storage, and deployment infrastructure only
when justified by concrete requirements.

## 17. Continuation prompt

When continuing in a new conversation, use this document as the project
context. First verify the actual current state of `probe.py` and Git
rather than assuming the latest proposed code was tested. Then make one
small, verifiable improvement at a time, explain the relevant networking
concept plainly, and keep PathLens separate from `systems-lab`.
