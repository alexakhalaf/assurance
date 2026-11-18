"""Build a synthetic fleet from the captured mock outputs.

Each synthetic device is a copy of the IOS-XE or NX-OS capture with its own hostname, its own
serial, and `--routes` extra OSPF routes, so state tables have realistic sizes. Scenario t2 churns
`--churn` percent of those routes (some withdrawn, some new) and keeps the template's own t1->t2
changes, so the diff engine has real work.

    python tools/loadtest/synth.py --devices 1000 --routes 1000 --out /var/tmp/synth
"""

from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "mock_data"
TEMPLATES = {"iosxe": "yyc-core-rtr01", "nxos": "yyc-dc-nxs01"}


def prefixes(n: int, offset: int = 0) -> list[str]:
    """Host routes in 100.64.0.0/10, distinct for every j."""
    return [f"100.{64 + j // 65536}.{(j // 256) % 256}.{j % 256}/32" for j in range(offset, offset + n)]


def iosxe_routes(pfx: list[str]) -> str:
    return "".join(f"O        {p} [110/20] via 10.10.1.2, 2d04h, GigabitEthernet1\n" for p in pfx)


def nxos_routes(pfx: list[str]) -> str:
    return "".join(f"{p}, ubest/mbest: 1/0\n    *via 10.10.1.1, Eth1/1, [110/20], 2d04h, ospf-1, intra\n" for p in pfx)


def build(out: Path, devices: int, routes: int, churn: float, nxos_share: float, seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    if out.exists():
        shutil.rmtree(out)
    base = prefixes(routes)
    k = int(routes * churn / 100)
    inventory = []
    for i in range(devices):
        platform = "nxos" if rnd.random() < nxos_share else "iosxe"
        tmpl = TEMPLATES[platform]
        name = f"lt-{platform}-{i:05d}"
        site = f"LT-SITE-{i // 50:03d}"
        for scen in ("t1", "t2"):
            pfx = base
            if scen == "t2" and k:
                drop = set(rnd.sample(range(routes), k))
                pfx = [p for j, p in enumerate(base) if j not in drop] + prefixes(k, offset=routes + i * k)
            src, dst = ROOT / scen / tmpl, out / scen / name
            dst.mkdir(parents=True)
            for f in src.iterdir():
                text = f.read_text().replace(tmpl, name)
                text = text.replace("9ABCDEF1234", f"LT{i:09d}").replace("9KV12345AB", f"LV{i:08d}")
                if f.name == "show_ip_route.txt" and platform == "iosxe":
                    text = text.rstrip("\n") + "\n" + iosxe_routes(pfx)
                elif f.name == "show_ip_route_vrf_all.txt" and platform == "nxos":
                    marker = '\nIP Route Table for VRF "management"'
                    head, sep, tail = text.partition(marker)
                    text = head.rstrip("\n") + "\n" + nxos_routes(pfx) + sep + tail
                (dst / f.name).write_text(text)
        inventory.append(
            {
                "name": name,
                "host": f"10.{200 + i // 65536}.{(i // 256) % 256}.{i % 256}",
                "platform": platform,
                "site": site,
                "role": "loadtest",
                "tags": "loadtest",
            }
        )
    return inventory


def csv(inventory: list[dict]) -> str:
    rows = ["name,host,platform,site,role,tags"]
    rows += [f"{d['name']},{d['host']},{d['platform']},{d['site']},{d['role']},{d['tags']}" for d in inventory]
    return "\n".join(rows) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--devices", type=int, default=1000)
    ap.add_argument("--routes", type=int, default=1000, help="extra routes per device")
    ap.add_argument("--churn", type=float, default=1.0, help="percent of extra routes changed in t2")
    ap.add_argument("--nxos-share", type=float, default=0.4)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    inv = build(a.out, a.devices, a.routes, a.churn, a.nxos_share)
    (a.out / "inventory.csv").write_text(csv(inv))
    print(f"{len(inv)} devices in {a.out} (inventory.csv); point ASSURANCE_MOCK_ROOT at it")


if __name__ == "__main__":
    main()
