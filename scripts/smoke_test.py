#!/usr/bin/env python3
"""HTTP smoke + form E2E against a running backend (and optional seeded build)."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def request(url, method="GET", headers=None, data=None):
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, method=method, headers=hdrs, data=body)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--preview-origin", default="http://localhost:3100")
    parser.add_argument(
        "--site-config",
        default=str(ROOT / "site_template" / ".local-site-config.json"),
        help="Path to seeded SITE_CONFIG JSON (from seed_local_preview.py)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        if ok:
            print(f"PASS {name}")
        else:
            fails += 1
            print(f"FAIL {name} {detail}")

    status, _, body = request(f"{base}/health")
    check("/health", status == 200 and json.loads(body) == {"ok": True}, body)

    status, _, body = request(f"{base}/ui/login")
    check("/ui/login", status == 200 and "AI Lead Pipeline" in body)

    status, headers, _ = request(
        f"{base}/preview-form",
        method="OPTIONS",
        headers={
            "Origin": args.preview_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    check(
        "preview-form CORS",
        status == 200 and headers.get("access-control-allow-origin") == args.preview_origin,
        str(headers.get("access-control-allow-origin")),
    )

    if args.admin_token:
        status, _, body = request(
            f"{base}/analytics/kpi",
            headers={"x-admin-token": args.admin_token},
        )
        check("authenticated KPI", status == 200 and "funnel" in json.loads(body), body[:200])

    config_path = Path(args.site_config)
    if config_path.exists():
        config = json.loads(config_path.read_text())
        status, _, body = request(
            f"{base}/preview-form",
            method="POST",
            headers={"Origin": args.preview_origin},
            data={
                "build_id": int(config["build_id"]),
                "form_token": config["form_token"],
                "name": "Smoke Tester",
                "contact": "+351911000222",
                "message": "Pedido de teste local",
            },
        )
        check("preview-form POST with seeded token", status == 200 and '"ok"' in body, f"{status} {body}")
    else:
        print(f"SKIP preview-form POST (no {config_path}; run scripts/seed_local_preview.py first)")

    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
