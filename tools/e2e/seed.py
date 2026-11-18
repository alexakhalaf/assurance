"""Seed a fresh mock deployment for the UI smoke test: two lab devices and the t1 and t2 snapshots.

    python tools/e2e/seed.py http://127.0.0.1:8000 admin <password>
"""
import sys
import time

import httpx

INVENTORY = """name,host,platform,site,role,tags
yyc-core-rtr01,10.10.0.1,iosxe,YYC-DC1,core-router,core
yyc-dc-nxs01,10.10.0.11,nxos,YYC-DC1,dc-spine,dc;spine
"""


def main(base: str, user: str, password: str) -> None:
    api = httpx.Client(base_url=base.rstrip("/") + "/api/v1", timeout=30)
    for _ in range(60):  # the API may still be starting
        try:
            if httpx.get(base.rstrip("/") + "/healthz", timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    token = api.post("/auth/login", json={"username": user, "password": password}).raise_for_status().json()["token"]
    api.headers["Authorization"] = f"Bearer {token}"
    api.post("/devices/import", params={"dry_run": "false"},
             files={"file": ("devices.csv", INVENTORY, "text/csv")}).raise_for_status()
    for scenario in ("t1", "t2"):
        body = {"mock_scenario": scenario, "labels": [f"e2e:{scenario}"]}
        snap = api.post("/snapshots", json=body).raise_for_status().json()
        for _ in range(120):
            snap = api.get(f"/snapshots/{snap['id']}").raise_for_status().json()
            if snap["status"] not in ("queued", "running", "finalizing"):
                break
            time.sleep(1)
        print(scenario, snap["id"], snap["status"])
        if snap["status"] != "complete":
            sys.exit(f"snapshot {scenario} ended {snap['status']}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
