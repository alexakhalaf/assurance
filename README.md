# Assurance snapshot collector (prototype)

Standalone tool that snapshots Cisco IOS-XE and NX-OS devices, stores timestamped
snapshots, and shows what changed between two points in time. No external source
of truth: devices come from a local `inventory.yaml`.

Pipeline: **inventory.yaml → pyATS/Unicon (SSH) → raw text → Genie parsers → normalized JSON → diff**

Collected per device: interfaces, IPv4 routing table (all VRFs on NX-OS), ARP, CDP and LLDP neighbors.

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'          # pulls pyats[library] (Genie + Unicon); Linux/macOS, Python 3.10-3.13
```

## Run on mock data (no lab needed)

```bash
assurance --store snapshots collect -i mock_data/inventory.yaml --mock-dir mock_data/t1 --at 2026-11-17T08:00:00Z
assurance --store snapshots collect -i mock_data/inventory.yaml --mock-dir mock_data/t2 --at 2026-11-18T08:00:00Z
assurance --store snapshots list
assurance --store snapshots diff                 # previous -> latest
assurance --store snapshots diff 24h latest      # what changed in the last 24 hours
assurance --store snapshots diff previous latest --json
pytest -q
```

`mock_data/t2` differs from `t1` by: rtr01 Gi3 link down (OSPF/connected routes, ARP and CDP
neighbor lost, ECMP collapses to one path), a new BGP prefix, a description change; nxs01 a new
static route, an ARP MAC move, and an ESXi host swap on Eth1/3 (LLDP + description).

## Run against real devices

```bash
cp inventory.example.yaml inventory.yaml        # edit
export DEVICE_USERNAME=... DEVICE_PASSWORD=...  # optional DEVICE_ENABLE_PASSWORD
assurance collect --filter site=YYC-DC1          # filters: site, role, platform, name, tag
assurance diff
```

## Snapshot layout

```
snapshots/<YYYYMMDDTHHMMSSZ>/
  manifest.json          id, time, devices, errors, parser warnings
  devices/<name>.json    normalized tables (what diffs run on)
  genie/<name>.json      full Genie output
  raw/<name>/<cmd>.txt   raw CLI text, so old snapshots can be re-parsed
```

Diff refs accept a snapshot id, `latest`, `previous`, an ISO time (nearest snapshot at or
before it), or an age like `90m`, `24h`, `7d`.

## Code map

| File | Role |
|---|---|
| `assurance/inventory.py` | Loads and filters `inventory.yaml` |
| `assurance/commands.py` | Commands per OS (each has a Genie parser) |
| `assurance/collectors.py` | `LiveCollector` (pyATS/Unicon) and `MockCollector` (files) |
| `assurance/genie_parse.py` | Offline Genie parsing of raw text |
| `assurance/normalize.py` | Genie → stable, diffable tables (drops counters, ages, timers) |
| `assurance/store.py` | Filesystem snapshot store + time-window resolution |
| `assurance/diff.py` | Added / removed / changed per table, text and JSON output |
| `assurance/cli.py` | `collect`, `list`, `diff` |

## Not done yet

- `LiveCollector` is written but untested against a real device.
- No HTTP API yet; `store.py` and `diff.py` are the pieces an API would wrap.
- IPv6, BGP neighbors, VLANs/MAC table, and other VRFs on IOS-XE are not collected yet.
