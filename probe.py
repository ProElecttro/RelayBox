import socket
import ssl
import time
import argparse

parser = argparse.ArgumentParser(
    description="PathLens network diagnostic probe"
)

parser.add_argument(
    "hostname",
    nargs="?",
    default="example.com",
    help="Hostname to diagnose, for example www.python.org"
)

args = parser.parse_args()

hostname = args.hostname
port = 443
timeout = 5

# --------------------------------------------------
# 1. DNS: Resolve the hostname into IP addresses
# --------------------------------------------------

dns_start = time.perf_counter()

try:
    addresses = socket.getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM
    )
except socket.gaierror as error:
    print(f"DNS resolution failed: {error}")
    raise SystemExit(1)

dns_elapsed = (time.perf_counter() - dns_start) * 1000

resolved_ips = sorted({address[4][0] for address in addresses})

print(f"DNS resolution successful for {hostname}")
print(f"DNS resolution time: {dns_elapsed:.2f} ms")

for ip in resolved_ips:
    print(f"Resolved IP: {ip}")


# --------------------------------------------------
# 2. TLS configuration
# --------------------------------------------------

context = ssl.create_default_context()
probe_completed = False


# --------------------------------------------------
# 3. TCP: Establish a connection
# 4. TLS: Establish a secure connection
# 5. HTTP: Send a request and inspect the response
# --------------------------------------------------

for family, socktype, proto, _, sockaddr in addresses:
    ip_version = (
        "IPv6" if family == socket.AF_INET6 else "IPv4"
    )

    try:
        with socket.socket(family, socktype, proto) as connection:
            connection.settimeout(timeout)

            # TCP connection
            tcp_start = time.perf_counter()
            connection.connect(sockaddr)
            tcp_elapsed = (
                time.perf_counter() - tcp_start
            ) * 1000

            remote_ip = connection.getpeername()[0]

            print("\nTCP connection successful!")
            print(f"IP version: {ip_version}")
            print(f"Remote address: {remote_ip}")
            print(f"TCP connection time: {tcp_elapsed:.2f} ms")

            # TLS handshake and certificate validation
            tls_start = time.perf_counter()

            with context.wrap_socket(
                connection,
                server_hostname=hostname
            ) as tls_connection:

                tls_elapsed = (
                    time.perf_counter() - tls_start
                ) * 1000

                certificate = tls_connection.getpeercert()

                print("\nTLS handshake successful!")
                print(f"TLS version: {tls_connection.version()}")
                print(f"TLS handshake time: {tls_elapsed:.2f} ms")
                print("Certificate validation: successful")
                print(
                    "Certificate expires: "
                    f"{certificate.get('notAfter', 'unknown')}"
                )

                # Send an HTTP request over the established TLS connection.
                request = (
                    f"GET / HTTP/1.1\r\n"
                    f"Host: {hostname}\r\n"
                    "User-Agent: PathLens/0.1\r\n"
                    "Accept: */*\r\n"
                    "Accept-Encoding: identity\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                )

                http_start = time.perf_counter()

                tls_connection.sendall(request.encode("ascii"))

                response_data = bytearray()
                time_to_first_byte_ms = None

                # Read the response until the server closes the connection.
                while True:
                    chunk = tls_connection.recv(4096)

                    if not chunk:
                        break

                    if time_to_first_byte_ms is None:
                        time_to_first_byte_ms = (
                            time.perf_counter() - http_start
                        ) * 1000

                    response_data.extend(chunk)

                http_elapsed = (
                    time.perf_counter() - http_start
                ) * 1000

                # Separate the HTTP headers from the response body.
                header_bytes, separator, body = bytes(
                    response_data
                ).partition(b"\r\n\r\n")

                if separator:
                    header_lines = header_bytes.decode(
                        "iso-8859-1"
                    ).split("\r\n")

                    status_line = header_lines[0]

                    print("\nHTTP response received!")
                    print(f"Status: {status_line}")

                    headers = {}

                    for line in header_lines[1:]:
                        if ":" in line:
                            name, value = line.split(":", 1)
                            headers[name.strip().lower()] = (
                                value.strip()
                            )

                    if time_to_first_byte_ms is not None:
                        print(
                            "HTTP time to first byte: "
                            f"{time_to_first_byte_ms:.2f} ms"
                        )

                    print(
                        "HTTP total response time: "
                        f"{http_elapsed:.2f} ms"
                    )
                    print(
                        "Response body bytes received: "
                        f"{len(body)}"
                    )

                    for name in (
                        "content-type",
                        "content-length",
                        "server",
                        "location",
                    ):
                        if name in headers:
                            print(f"{name}: {headers[name]}")
                else:
                    print(
                        "\nCould not parse a complete "
                        "HTTP response header."
                    )

                probe_completed = True
                break

    except ssl.SSLCertVerificationError as error:
        print(f"Certificate validation failed: {error}")

    except ssl.SSLError as error:
        print(f"TLS handshake failed: {error}")

    except OSError as error:
        print(
            f"Connection or HTTP request failed "
            f"for {sockaddr[0]}: {error}"
        )


if not probe_completed:
    print("\nCould not complete the probe successfully.")
