"""Provision independent test tenants using EchoMem's public bootstrap API."""

import argparse
import json
import os
from pathlib import Path
import time

from performance.targets.echomem.probes._client import EchoMemHTTP


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--count", type=int, default=32)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prefix", default="six-metric-test")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("count must be positive")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    tenants = []
    stamp = int(time.time())
    try:
        for index in range(args.count):
            client = EchoMemHTTP(args.base_url, timeout_s=15, agent_id="default")
            identity = client.provision_isolated_identity(f"{args.prefix}-{stamp}-{index}")
            tenants.append(identity)
            args.out.write_text(json.dumps({"tenants": tenants}, indent=2) + "\n", encoding="utf-8")
            client.open_session(identity["tenant_id"], "identity-preflight")
            print(f"verified independent tenant {index + 1}/{args.count}", flush=True)
    except Exception as exc:
        print(f"Provisioning stopped: {type(exc).__name__}; {len(tenants)} identities saved to {args.out}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
