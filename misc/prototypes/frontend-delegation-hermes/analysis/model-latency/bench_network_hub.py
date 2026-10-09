"""Network path to the Inference Hub from this machine: DNS, TCP connect and TLS handshake time to
inference-api.nvidia.com:443 (no request, no key). Hermes opens a new client (new TCP+TLS connection) for
every backend LLM call, so this cost is paid per call; the frontend keeps one connection alive.
Run it on each eval machine to compare paths.

    python3 bench_network_hub.py -n 8
"""
import argparse
import json
import socket
import ssl
import time

import bench_common as bc

HOST = "inference-api.nvidia.com"

ap = argparse.ArgumentParser()
ap.add_argument("-n", type=int, default=8)
a = ap.parse_args()
ctx = ssl.create_default_context()
rows = []
started = bc.now_utc()
for i in range(a.n):
    t0 = time.perf_counter()
    addr = socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)[0][4]
    t1 = time.perf_counter()
    s = socket.create_connection(addr[:2], timeout=10)
    t2 = time.perf_counter()
    tls = ctx.wrap_socket(s, server_hostname=HOST)
    t3 = time.perf_counter()
    tls.close()
    row = {"ok": True, "i": i, "ip": addr[0], "dns_ms": round((t1 - t0) * 1000, 1),
           "tcp_connect_ms": round((t2 - t1) * 1000, 1), "tls_handshake_ms": round((t3 - t2) * 1000, 1),
           "new_connection_ms": round((t3 - t1) * 1000, 1)}
    rows.append(row)
    print(json.dumps(row))
    time.sleep(0.3)
bc.write_result("network_hub", {"host": HOST, "started_utc": started, "ended_utc": bc.now_utc(),
                                "machine": socket.gethostname()}, rows,
                ["dns_ms", "tcp_connect_ms", "tls_handshake_ms", "new_connection_ms"])
