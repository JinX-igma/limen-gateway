#!/usr/bin/env python3
"""Headless device management: mint/revoke/list device identities directly
against the gateway's SQLite store, without needing the admin HTTP API up.

Useful for SSH-only operators, or for minting the very first device before
EDGE_ADMIN_CORS_ORIGIN is configured. The admin HTTP API (admin_api.py)
calls the exact same DevicesStore methods — this is not a second source
of truth.
"""
from __future__ import print_function

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from edge_gateway.devices_store import DeviceError, DevicesStore  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Manage Limen gateway device identities")
    parser.add_argument(
        "--database",
        default=os.environ.get(
            "EDGE_DATABASE",
            os.path.join(os.path.dirname(__file__), "..", "data", "gateway.sqlite3"),
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    mint = sub.add_parser("mint", help="create a new device identity, print its token once")
    mint.add_argument("--label", default=None)
    mint.add_argument("--id", default=None, help="explicit device id (default: random)")

    revoke = sub.add_parser("revoke", help="revoke a device's token")
    revoke.add_argument("device_id")

    sub.add_parser("list", help="list all device identities")

    args = parser.parse_args()
    store = DevicesStore(args.database)

    if args.command == "mint":
        record = store.mint(label=args.label, device_id=args.id)
        print("Device id:    {}".format(record["id"]))
        print("Device token: {}".format(record["token"]))
        print("Paste this token into the device's captive-portal setup page.")
        print("It will not be shown again.")
        return 0

    if args.command == "revoke":
        try:
            store.revoke(args.device_id)
        except DeviceError as exc:
            print("error: {}".format(exc), file=sys.stderr)
            return 1
        print("Revoked {}".format(args.device_id))
        return 0

    if args.command == "list":
        devices = store.list()
        if not devices:
            print("(no devices registered)")
        for device in devices:
            status = "revoked" if device["revoked"] else "active"
            print("{}\t{}\t{}".format(device["id"], status, device["label"] or ""))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
