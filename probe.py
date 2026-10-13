from datetime import datetime, timezone
from pathlib import Path
import uuid
import argparse
import ipaddress
import json
import socket
import ssl
import sys
import time
from urllib.parse import urljoin, urlsplit


def make_result(stage, outcome, target, elapsed_ms):
    return {
        "stage": stage,
        "outcome": outcome,
        "target": target,
        "elapsed_ms": round(elapsed_ms, 2),
    }


def save_history(report, history_path):
    """Append one complete probe report as a JSONL record."""
    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as history_file:
        history_file.write(
            json.dumps(report, separators=(",", ":")) + "\n"
        )



def classify_http_status(status_code):
    if 200 <= status_code < 300:
        return "HEALTHY"
    if 300 <= status_code < 400:
        return "REDIRECT"
    if 400 <= status_code < 500:
        return "CLIENT_ERROR"
    if 500 <= status_code < 600:
        return "SERVER_ERROR"
    return "INVALID_STATUS"



def format_address(address, port):
    """Format IPv4 and IPv6 addresses unambiguously."""
    if ":" in address:
        return f"[{address}]:{port}"
    return f"{address}:{port}"


def probe_url(url, context, timeout, verbose, ip_override=None):
    parsed = urlsplit(url)
    hostname = parsed.hostname

    def log(*args):
        if verbose:
            print(*args, file=sys.stderr)

    # Validate the URL scheme and hostname.
    if parsed.scheme != "https" or not hostname:
        return {
            "url": url,
            "outcome": "FAILURE",
            "results": [
                make_result(
                    "URL_PARSE",
                    "UNSUPPORTED_URL",
                    url,
                    0,
                )
            ],
        }, None

    try:
        port = parsed.port or 443
    except ValueError:
        return {
            "url": url,
            "outcome": "FAILURE",
            "results": [
                make_result(
                    "URL_PARSE",
                    "INVALID_PORT",
                    url,
                    0,
                )
            ],
        }, None

    if not 1 <= port <= 65535:
        return {
            "url": url,
            "outcome": "FAILURE",
            "results": [
                make_result(
                    "URL_PARSE",
                    "INVALID_PORT",
                    url,
                    0,
                )
            ],
        }, None

    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"

    results = []

    # Resolve the hostname or select an explicitly supplied IP.
    stage_start = time.perf_counter()

    if ip_override is not None:
        try:
            ip_obj = ipaddress.ip_address(ip_override)
        except ValueError:
            return {
                "url": url,
                "outcome": "FAILURE",
                "results": [
                    make_result(
                        "IP_SELECT",
                        "INVALID_IP",
                        ip_override,
                        0,
                    )
                ],
            }, None

        family = (
            socket.AF_INET6
            if ip_obj.version == 6
            else socket.AF_INET
        )

        stage_name = "IP_SELECT"
        stage_target = str(ip_obj)

        try:
            addresses = socket.getaddrinfo(
                str(ip_obj),
                port,
                family=family,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as error:
            elapsed = (
                time.perf_counter() - stage_start
            ) * 1000

            results.append(
                make_result(
                    stage_name,
                    "FAILURE",
                    stage_target,
                    elapsed,
                )
            )
            log(f"IP selection failed: {error}")

            return {
                "url": url,
                "outcome": "FAILURE",
                "results": results,
            }, None

    else:
        stage_name = "DNS_RESOLVE"
        stage_target = hostname

        try:
            addresses = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as error:
            elapsed = (
                time.perf_counter() - stage_start
            ) * 1000

            results.append(
                make_result(
                    stage_name,
                    "FAILURE",
                    hostname,
                    elapsed,
                )
            )
            log(f"DNS resolution failed for {hostname}: {error}")

            return {
                "url": url,
                "outcome": "FAILURE",
                "results": results,
            }, None

    elapsed = (time.perf_counter() - stage_start) * 1000

    results.append(
        make_result(
            stage_name,
            "SUCCESS",
            stage_target,
            elapsed,
        )
    )

    log(
        f"{stage_name} completed for {stage_target} "
        f"in {elapsed:.2f} ms"
    )

    # Try each resolved address until one completes the HTTP exchange.
    for family, socktype, proto, _, sockaddr in addresses:
        connected_ip = sockaddr[0]
        address_target = format_address(connected_ip, port)

        stage = "TCP_CONNECT"
        stage_start = time.perf_counter()

        try:
            with socket.socket(family, socktype, proto) as connection:
                connection.settimeout(timeout)
                connection.connect(sockaddr)

                elapsed = (
                    time.perf_counter() - stage_start
                ) * 1000

                results.append(
                    make_result(
                        "TCP_CONNECT",
                        "SUCCESS",
                        address_target,
                        elapsed,
                    )
                )

                log(
                    f"TCP connected to {address_target} "
                    f"in {elapsed:.2f} ms"
                )

                # Keep the original hostname for TLS SNI and
                # certificate hostname verification.
                stage = "TLS_HANDSHAKE"
                stage_start = time.perf_counter()

                with context.wrap_socket(
                    connection,
                    server_hostname=hostname,
                ) as secure_connection:
                    elapsed = (
                        time.perf_counter() - stage_start
                    ) * 1000

                    results.append(
                        make_result(
                            "TLS_HANDSHAKE",
                            "SUCCESS",
                            hostname,
                            elapsed,
                        )
                    )

                    log(
                        f"TLS {secure_connection.version()} "
                        f"completed in {elapsed:.2f} ms"
                    )

                    # Send HTTP using the hostname, not the IP.
                    stage = "HTTP_REQUEST"
                    stage_start = time.perf_counter()

                    request = (
                        f"GET {path} HTTP/1.1\r\n"
                        f"Host: {hostname}\r\n"
                        "User-Agent: PathLens/0.2\r\n"
                        "Accept: */*\r\n"
                        "Accept-Encoding: identity\r\n"
                        "Connection: close\r\n"
                        "\r\n"
                    )

                    secure_connection.sendall(
                        request.encode("ascii")
                    )

                    response_data = bytearray()
                    ttfb_ms = None

                    # Read until the server closes the connection.
                    while True:
                        chunk = secure_connection.recv(4096)

                        if not chunk:
                            break

                        if ttfb_ms is None:
                            ttfb_ms = (
                                time.perf_counter()
                                - stage_start
                            ) * 1000

                        response_data.extend(chunk)

                    elapsed = (
                        time.perf_counter() - stage_start
                    ) * 1000

                    header_bytes, separator, body = bytes(
                        response_data
                    ).partition(b"\r\n\r\n")

                    if not separator:
                        results.append(
                            make_result(
                                "HTTP_REQUEST",
                                "INVALID_RESPONSE",
                                hostname,
                                elapsed,
                            )
                        )

                        return {
                            "url": url,
                            "outcome": "FAILURE",
                            "connected_ip": connected_ip,
                            "port": port,
                            "results": results,
                        }, None

                    header_lines = header_bytes.decode(
                        "iso-8859-1"
                    ).split("\r\n")

                    status_parts = header_lines[0].split()

                    if len(status_parts) < 2:
                        results.append(
                            make_result(
                                "HTTP_REQUEST",
                                "INVALID_STATUS_LINE",
                                hostname,
                                elapsed,
                            )
                        )

                        return {
                            "url": url,
                            "outcome": "FAILURE",
                            "connected_ip": connected_ip,
                            "port": port,
                            "results": results,
                        }, None

                    try:
                        status_code = int(status_parts[1])
                    except ValueError:
                        results.append(
                            make_result(
                                "HTTP_REQUEST",
                                "INVALID_STATUS_CODE",
                                hostname,
                                elapsed,
                            )
                        )

                        return {
                            "url": url,
                            "outcome": "FAILURE",
                            "connected_ip": connected_ip,
                            "port": port,
                            "results": results,
                        }, None

                    headers = {}

                    for line in header_lines[1:]:
                        if ":" in line:
                            name, value = line.split(":", 1)
                            headers[name.strip().lower()] = (
                                value.strip()
                            )

                    http_result = make_result(
                        "HTTP_REQUEST",
                        "SUCCESS",
                        hostname,
                        elapsed,
                    )

                    http_result["status_code"] = status_code
                    http_result["http_health"] = classify_http_status(status_code)

                    if ttfb_ms is not None:
                        http_result["time_to_first_byte_ms"] = round(
                            ttfb_ms,
                            2,
                        )

                    if "location" in headers:
                        http_result["redirect_location"] = (
                            headers["location"]
                        )

                    results.append(http_result)

                    log(
                        f"HTTP {status_code} from {hostname}; "
                        f"{len(body)} body bytes received"
                    )

                    redirect_url = None

                    if (
                        status_code in (301, 302, 303, 307, 308)
                        and "location" in headers
                    ):
                        redirect_url = urljoin(
                            url,
                            headers["location"],
                        )

                    return {
                        "url": url,
                        "outcome": "SUCCESS",
                        "connected_ip": connected_ip,
                        "port": port,
                        "status_code": status_code,
                        "results": results,
                    }, redirect_url

        except TimeoutError as error:
            elapsed = (
                time.perf_counter() - stage_start
            ) * 1000

            target = (
                address_target
                if stage == "TCP_CONNECT"
                else hostname
            )

            results.append(
                make_result(
                    stage,
                    "TIMEOUT",
                    target,
                    elapsed,
                )
            )

            log(f"{stage} timed out for {target}: {error}")

        except ssl.SSLCertVerificationError as error:
            elapsed = (
                time.perf_counter() - stage_start
            ) * 1000

            results.append(
                make_result(
                    "TLS_HANDSHAKE",
                    "CERTIFICATE_ERROR",
                    hostname,
                    elapsed,
                )
            )

            log(f"Certificate validation failed: {error}")

        except ssl.SSLError as error:
            elapsed = (
                time.perf_counter() - stage_start
            ) * 1000

            results.append(
                make_result(
                    stage,
                    "FAILURE",
                    hostname,
                    elapsed,
                )
            )

            log(f"{stage} failed for {hostname}: {error}")

        except OSError as error:
            elapsed = (
                time.perf_counter() - stage_start
            ) * 1000

            target = (
                address_target
                if stage == "TCP_CONNECT"
                else hostname
            )

            results.append(
                make_result(
                    stage,
                    "FAILURE",
                    target,
                    elapsed,
                )
            )

            log(f"{stage} failed for {target}: {error}")

    return {
        "url": url,
        "outcome": "FAILURE",
        "results": results,
    }, None


def main():
    parser = argparse.ArgumentParser(
        description="PathLens network diagnostic probe"
    )

    parser.add_argument(
        "hostname",
        nargs="?",
        default="example.com",
        help="Hostname or HTTPS URL to diagnose",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Write diagnostic messages to stderr",
    )

    parser.add_argument(
        "--max-redirects",
        type=int,
        default=5,
        help="Maximum number of HTTP redirects to follow",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=5,
        help="Socket timeout in seconds",
    )

    parser.add_argument(
        "--ip",
        help="Connect to a specific IP for the initial URL",
    )

    parser.add_argument(
        "--history",
        default="pathlens_history.jsonl",
        help="File to append probe history to",
    )

    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Do not save this run to history",
    )

    args = parser.parse_args()

    if args.max_redirects < 0:
        parser.error("--max-redirects must be zero or greater")

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    initial_url = args.hostname

    if "://" not in initial_url:
        initial_url = f"https://{initial_url}/"

    context = ssl.create_default_context()

    hops = []
    visited_urls = set()
    current_url = initial_url
    redirects_followed = 0
    overall_outcome = "FAILURE"

    while True:
        if current_url in visited_urls:
            overall_outcome = "REDIRECT_LOOP"
            break

        visited_urls.add(current_url)

        hop, redirect_url = probe_url(
            current_url,
            context,
            args.timeout,
            args.verbose,
            args.ip if redirects_followed == 0 else None,
        )

        hops.append(hop)

        if hop["outcome"] != "SUCCESS":
            overall_outcome = "FAILURE"
            break

        if redirect_url is None:
            overall_outcome = "SUCCESS"
            break

        if redirects_followed >= args.max_redirects:
            overall_outcome = "REDIRECT_LIMIT"
            break

        redirects_followed += 1

        if args.verbose:
            print(
                f"Following redirect to {redirect_url}",
                file=sys.stderr,
            )

        current_url = redirect_url

    final_status = hops[-1].get("status_code") if hops else None

    if overall_outcome != "SUCCESS":
        application_outcome = "UNKNOWN"
    elif final_status is None:
        application_outcome = "UNKNOWN"
    else:
        application_outcome = classify_http_status(final_status)


    report = {
        "run_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z"),
        "target": args.hostname,
        "overall_outcome": overall_outcome,
        "redirects_followed": redirects_followed,
        "hops": hops,
    }

    if not args.no_history:
        try:
            save_history(report, args.history)
        except OSError as error:
            print(
                f"PathLens: could not save history: {error}",
                file=sys.stderr,
            )

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

