#!/usr/bin/env python3
"""One-time environment bootstrap: local CA + server certificate + env file.

Device identities are NOT minted here. Use scripts/manage-devices.py
(headless, no running server required) or the gateway's admin HTTP API
(POST /admin/devices, once the server is up) instead — both go through
the same edge_gateway.devices_store.DevicesStore.
"""
from __future__ import print_function

import argparse
import os
import secrets
import socket
import subprocess
import sys


def write_private(path, text):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)


def main():
    parser = argparse.ArgumentParser(description="Provision gateway TLS and environment")
    parser.add_argument("--lan-ip", required=True, help="LAN IPv4 address used by devices")
    parser.add_argument(
        "--hostname", default="limen-gateway.local", help="mDNS/CN hostname for the server cert"
    )
    args = parser.parse_args()
    socket.inet_aton(args.lan_ip)

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    secret_dir = os.path.join(root, ".secrets")
    tls_dir = os.path.join(secret_dir, "tls")
    for directory in (
        secret_dir,
        tls_dir,
        os.path.join(root, "data"),
        os.path.join(root, "logs"),
        os.path.join(root, "runtime", "agent-empty"),
    ):
        if not os.path.isdir(directory):
            os.makedirs(directory, 0o700)

    protected = [
        os.path.join(secret_dir, "gateway.env"),
        os.path.join(tls_dir, "ca.key"),
        os.path.join(tls_dir, "ca.crt"),
        os.path.join(tls_dir, "server.key"),
        os.path.join(tls_dir, "server.crt"),
    ]
    existing = [path for path in protected if os.path.exists(path)]
    if existing:
        print("Refusing to overwrite existing secrets:", file=sys.stderr)
        for path in existing:
            print("  " + path, file=sys.stderr)
        return 2

    openssl_config = os.path.join(tls_dir, "openssl.cnf")
    write_private(
        openssl_config,
        """[req]
distinguished_name = dn
req_extensions = extensions
prompt = no
[dn]
CN = {hostname}
[extensions]
subjectAltName = @alt_names
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
[alt_names]
DNS.1 = {hostname}
DNS.2 = localhost
IP.1 = {lan_ip}
IP.2 = 127.0.0.1
""".format(hostname=args.hostname, lan_ip=args.lan_ip),
    )

    key_path = os.path.join(tls_dir, "server.key")
    cert_path = os.path.join(tls_dir, "server.crt")
    ca_key_path = os.path.join(tls_dir, "ca.key")
    ca_cert_path = os.path.join(tls_dir, "ca.crt")
    subprocess.check_call(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-days", "3650", "-subj", "/CN=Limen Gateway Local CA",
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout", ca_key_path, "-out", ca_cert_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    csr_path = os.path.join(tls_dir, "server.csr")
    subprocess.check_call(
        [
            "openssl", "req", "-new", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-config", openssl_config, "-keyout", key_path, "-out", csr_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        [
            "openssl", "x509", "-req", "-in", csr_path, "-CA", ca_cert_path,
            "-CAkey", ca_key_path, "-CAcreateserial", "-days", "825", "-sha256",
            "-extfile", openssl_config, "-extensions", "extensions", "-out", cert_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.unlink(csr_path)
    for path in (ca_key_path, key_path):
        os.chmod(path, 0o600)
    for path in (ca_cert_path, cert_path):
        os.chmod(path, 0o644)

    admin_token = secrets.token_urlsafe(32)
    environment = """EDGE_ROOT={root}
EDGE_BIND=0.0.0.0
EDGE_PORT=8443
EDGE_TLS_CERT={cert}
EDGE_TLS_KEY={key}
EDGE_ADMIN_BIND=127.0.0.1
EDGE_ADMIN_PORT=8444
EDGE_ADMIN_TLS_CERT={cert}
EDGE_ADMIN_TLS_KEY={key}
EDGE_ADMIN_TOKEN={admin_token}
EDGE_ADMIN_CORS_ORIGIN=
EDGE_DATABASE={root}/data/gateway.sqlite3
EDGE_BACKEND=openai_api
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
EDGE_AGENT_WORK_DIR={root}/runtime/agent-empty
EDGE_CODEX_BIN=codex
EDGE_CODEX_MODEL=
EDGE_REQUEST_TIMEOUT_SECONDS=180
EDGE_REQUESTS_PER_MINUTE=10
EDGE_MAX_CONCURRENT=2
EDGE_MAX_REQUEST_BYTES=32768
EDGE_MAX_OUTPUT_CHARS=12000
""".format(root=root, cert=cert_path, key=key_path, admin_token=admin_token)
    write_private(os.path.join(secret_dir, "gateway.env"), environment)

    fingerprint = subprocess.check_output(
        ["openssl", "x509", "-in", ca_cert_path, "-noout", "-fingerprint", "-sha256"],
        universal_newlines=True,
    ).strip()
    print("Provisioned TLS for both listeners and a fresh admin token.")
    print("Device API:  https://{}:8443".format(args.lan_ip))
    print("Admin API:   https://127.0.0.1:8444 (loopback only by default)")
    print("Env file:    {}".format(os.path.join(secret_dir, "gateway.env")))
    print("Pinned CA:   {}".format(ca_cert_path))
    print(fingerprint)
    print()
    print("Set OPENAI_API_KEY (or switch EDGE_BACKEND=codex) in the env file, then:")
    print('  python3 scripts/manage-devices.py mint --label "my-esp32"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
