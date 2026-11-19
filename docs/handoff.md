# Assurance technical handoff

For whoever maintains Assurance next. It describes how the system is built, how to run, deploy and
change it, and what is still unproven. The README has the full configuration table and API list;
this document explains how the parts fit and where the risks are. The user-facing guide is
[user-guide.md](user-guide.md).

State at handoff (19 November 2026): release 0.5 (beta) on `main`, merged through PR 5. PR 6
("Hide the lab (mock scenario) option on live servers") may still be open.

## 1. What it is

A standalone network assurance service for Cisco IOS-XE and NX-OS (target fleet: up to 500
devices, load tested at 1,000). It keeps its own inventory and credentials, collects operational
state over SSH, parses it with Cisco Genie, stores every snapshot, and answers "what changed
between A and B". It has no dependency on any external source of truth or orchestrator (Nautobot,
Itential and similar were ruled out on purpose).

API first: every feature is under `/api/v1`. The React UI and the `assurance` CLI are both API
clients.

## 2. Architecture

```
            Browser / CLI / pipelines
                      │ HTTPS
              ┌───────▼────────┐
              │ Caddy (Compose)│  or Ingress (Helm)
              └───────┬────────┘
              ┌───────▼────────┐        ┌──────────────┐
              │  API (FastAPI) │───────▶│ PostgreSQL 16│  inventory, snapshots, state rows,
              │  + static UI   │        └──────▲───────┘  checks, users, audit
              └───┬───────┬────┘               │
        enqueue   │       │ read raw           │
              ┌───▼───┐   │             ┌──────┴───────┐
              │ Redis │◀──┼─────────────│   Workers    │──SSH (pyATS/Unicon)──▶ devices
              └───▲───┘   │             │   (RQ)       │
                  │       │             └──────┬───────┘
          leader  │       └─────────┐          │ raw text + Genie JSON
           lock   │           ┌─────▼──────────▼─┐
              ┌───┴───────┐   │  S3 object store │  (SeaweedFS in Compose; any S3)
              │ Scheduler │   └──────────────────┘
              └───────────┘
```

**Snapshot lifecycle** (`assurance/services/snapshots.py`):

1. `POST /snapshots` (or a schedule) calls `create_snapshot`: resolves the scope to active devices,
   creates a `Snapshot` (status `queued`) and one `DeviceCollection` per device (`pending`), and
   enqueues one RQ job per device. Returns 202.
2. A worker runs `run_device_collection`: takes a per-device Redis lock, waits a random jitter,
   resolves credentials, checks the pinned SSH host key (`collection/hostkeys.py`), then runs the
   commands (`collection/collectors.py`).
3. `collection/pipeline.py` masks secrets in every output, parses each command with Genie, and
   normalizes to platform-neutral tables (`collection/normalize.py`, `collection/configtext.py`). A
   command that times out, is refused (TACACS) or fails to parse does not sink the device: the
   tables it feeds are recorded in `device_collections.failed_tables` and the device is `partial`.
4. Raw text and full Genie output go to S3, content-addressed (identical outputs are stored once).
   Normalized rows go to `state_rows` with a content hash.
5. When the last device finishes, `maybe_finalize` → `finalize`: snapshot status (`complete`,
   `partial`, `failed`), change summary against the baseline, intent check grading, webhook.
6. The scheduler process runs cron schedules, report schedules, retention, object GC and
   `reap_stale` (marks collections stuck past `STALE_*_MINUTES` as failed so snapshots finish). Two
   schedulers can run; a Redis lock keeps one active.

**Diff** (`services/diff.py`): rows are joined on (device, table, key). Only in B = added, only in
A = removed, both with different hashes = changed (with `fields_changed`). Volatile fields are
dropped by the normalizers, so they never get hashed. Devices that failed in either snapshot are
`devices_unknown`; tables that failed are `tables_unknown`. `previous` and time windows pick a
baseline that covers at least 90% of the same devices, so a one-device ad-hoc snapshot never
becomes the fleet baseline.

**Checks** (`services/checks.py`): 16 built-in state rules, 8 built-in delta rules, and custom
rules (`row`, `config_require`, `config_forbid`) stored as data. `prepost()` runs delta rules plus
"state rules that newly fail", applies a check profile if one matches, and returns pass / warning /
fail.

## 3. Stack

| Layer | Choice |
|---|---|
| Language | Python 3.13 (backend), TypeScript (UI) |
| Parsing and SSH | `pyats[library]` 26.x (Genie parsers, Unicon), pinned per major |
| API | FastAPI, Uvicorn, Pydantic 2 |
| Database | PostgreSQL 16, SQLAlchemy 2, Alembic; `pg_trgm` extension for search indexes |
| Jobs | Redis 7 + RQ |
| Object store | S3 API via boto3; SeaweedFS bundled in Compose (MinIO community images are no longer pullable); `filesystem` backend for labs and tests |
| UI | React, Vite, ECharts |
| Auth | Argon2 passwords, SHA-256-hashed tokens, OIDC (code flow + PKCE) |
| Crypto | AES-256-GCM envelope encryption (`cryptography`) |
| Observability | Prometheus metrics, JSON logs, optional OpenTelemetry (`otel` extra) |
| Packaging | One image (`Dockerfile`) for api, worker, scheduler, migrate and backup roles |
| Deploy | Docker Compose with Caddy for TLS; Helm chart for Kubernetes |

Exact versions are pinned in `constraints.txt`; CI and the image install with `-c constraints.txt`.

## 4. Repository layout

| Path | Contents |
|---|---|
| `assurance/collection/` | `commands.py` (commands per OS and the table they feed), `collectors.py` (live and mock), `genie_parse.py`, `normalize.py`, `configtext.py` (secret masking, config sections, SNMP and AAA tables), `pipeline.py`, `hostkeys.py`, `target.py` |
| `assurance/services/` | `snapshots.py`, `diff.py`, `checks.py`, `inventory.py`, `credentials.py`, `schedules.py`, `retention.py`, `search.py`, `discovery.py`, `report_mail.py`, `backup.py`, `audit.py` |
| `assurance/api/` | `app.py` (app factory, middleware, security headers, static UI), `deps.py` (auth and roles), `schemas.py`, `errors.py` (RFC 9457 problem JSON), `routes/*.py` |
| `assurance/security/` | `crypto.py`, `passwords.py`, `oidc.py`, `ratelimit.py` |
| `assurance/cli/` | `client.py` (`assurance`, an API client), `admin.py` (`assurance-admin`) |
| `assurance/models.py`, `assurance/migrations/` | Schema and Alembic migrations `0001`–`0010` |
| `assurance/config.py` | All settings (`ASSURANCE_*` environment variables) |
| `assurance/jobs.py`, `storage.py`, `metrics.py`, `tracing.py`, `logging_setup.py`, `exports.py` | Infrastructure |
| `frontend/` | React UI; `npm run build` writes to `assurance/ui`, which the API serves |
| `mock_data/t1`, `mock_data/t2` | Hand-written IOS-XE and NX-OS captures for two lab devices, verified with Genie; `t2` is `t1` plus known changes |
| `tests/` | pytest suite (see section 11) |
| `tools/loadtest/` | Synthetic fleet generator and load test runner |
| `tools/e2e/` | Seed script and Playwright smoke test of 32 UI pages |
| `deploy/` | `Caddyfile`, `init-secrets.sh`, `helm/assurance` |
| `docker-compose.yml`, `Dockerfile`, `.env.example` | Single-server deployment |
| `.github/workflows/ci.yml` | CI |
| `.trivyignore` | Accepted image advisories with reasons |
| `docs/` | This document, the user guide, the load test report, screenshots |

## 5. Configuration (`.env`)

Every setting is an environment variable prefixed `ASSURANCE_` (except the Compose-only database,
Redis and S3 passwords). In Compose they come from `.env`; in Helm from `config:` in
`values.yaml` plus a Secret. The README's Configuration table lists them all. The ones that matter
most:

| Variable | Why it matters |
|---|---|
| `ASSURANCE_HOSTNAME`, `ASSURANCE_TLS` | Compose: the name Caddy serves and how it gets a certificate (`internal`, an e-mail for Let's Encrypt, or cert/key paths under `secrets/tls/`). |
| `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Compose service passwords. Change all of them. |
| `ASSURANCE_BOOTSTRAP_ADMIN_PASSWORD` | Creates user `admin` on the first `migrate` when no user exists. |
| `ASSURANCE_MASTER_KEY_FILE` | The credential store's master key (Compose: `secrets/master.key` from `init-secrets.sh`). Not in backups. Losing it means re-entering every device credential. |
| `ASSURANCE_COLLECTOR` | `live` (SSH) or `mock` (reads `ASSURANCE_MOCK_ROOT`). |
| `ASSURANCE_SSH_HOST_KEY_POLICY` | `tofu` (default), `strict`, `off`. |
| `ASSURANCE_RETENTION_DAYS`, `ASSURANCE_RETENTION_THIN_AFTER_DAYS` | `.env.example` and Helm set 90 and 7. Code default is 0 (keep everything). |
| `ASSURANCE_WORKERS`, `ASSURANCE_API_WORKERS` | Compose scale. About 65 devices per minute per worker with mock data. |
| `ASSURANCE_WEBHOOK_SECRET`, `ASSURANCE_WEBHOOK_ALLOWED_HOSTS` | Webhook signing; private targets are refused unless listed. |
| `ASSURANCE_OIDC_*`, `ASSURANCE_LOCAL_LOGIN` | SSO. `OIDC_DEFAULT_ROLE=none` refuses users with no mapped group. |
| `ASSURANCE_SMTP_*` | Scheduled report e-mail. |
| `ASSURANCE_TRUSTED_PROXIES` | Only these may set `X-Forwarded-For` (rate limits and audit IPs depend on it). |
| `ASSURANCE_METRICS_TOKEN` | Protects `/metrics`. |
| `ASSURANCE_OTEL_ENDPOINT` | Turns on tracing. |
| `ASSURANCE_ENV` | `dev`, `test` or `prod` (Helm sets `prod`). |

Never commit `.env` or `secrets/`; both are in `.gitignore`.

## 6. Deployment

### Docker Compose (single server)

```bash
cp .env.example .env           # set every password, ASSURANCE_HOSTNAME and ASSURANCE_TLS
./deploy/init-secrets.sh       # creates secrets/master.key and secrets/s3.json
docker compose up -d --build
```

Services: `postgres`, `redis`, `objectstore` (SeaweedFS), `migrate` (runs once), `api` (bound to
127.0.0.1), `proxy` (Caddy, the only published port, 443), `worker` (scaled by
`ASSURANCE_WORKERS`), `scheduler`, `backup` (daily archive to the `backups` volume, keeps
`ASSURANCE_BACKUP_KEEP_DAYS`). App containers run as UID 10001 on read-only file systems.

Upgrades: pull, `docker compose up -d --build`; `migrate` applies schema changes first. Read the
README's Upgrading section: migrations 0008 and 0009 build indexes concurrently, and some
normalization changes show once in the first diff across an upgrade.

### Helm (Kubernetes, HA)

`deploy/helm/assurance`. PostgreSQL, Redis and S3 are external (use HA services). The chart runs:
API (2 replicas, optional HPA), workers (3, optional HPA), scheduler (2, one active through the
Redis lock), a pre-install/pre-upgrade migration Job, an optional backup CronJob, Ingress,
PodDisruptionBudgets, topology spreading, NetworkPolicy and an optional ServiceMonitor. Pods are
non-root, read-only root filesystem, no service account token.

```bash
assurance-admin gen-master-key --out master.key
kubectl create secret generic assurance \
  --from-literal=database-url='postgresql+psycopg://…?sslmode=require' \
  --from-literal=redis-url='redis://:…@redis:6379/0' \
  --from-literal=s3-access-key=… --from-literal=s3-secret-key=… \
  --from-file=master-key=master.key --from-literal=bootstrap-admin-password=…
helm install assurance deploy/helm/assurance --set existingSecret=assurance \
  --set ingress.enabled=true,ingress.host=assurance.example.net,ingress.tls.secretName=assurance-tls \
  --set backup.enabled=true
```

The chart is linted, rendered and schema-validated in CI but has **never been installed on a real
cluster**.

### Operations

- Health: `/healthz` (process), `/readyz` (database, Redis, object store).
- Metrics: `/metrics` (Prometheus). Logs: JSON with `request_id` and `trace_id`.
- Admin CLI (`assurance-admin`): `migrate`, `create-user`, `reset-password`, `gen-master-key`,
  `worker`, `scheduler`, `prune`, `gc-objects`, `backup`, `backup-loop`, `restore`.
- Backup is one tar file: `pg_dump` (custom format), referenced objects, and a manifest with schema
  revision and checksum. Restore needs `pg_restore` matching the server major version (16); stop
  API, workers and scheduler first. Restores into newer releases by migrating after restore.

## 7. CI

`.github/workflows/ci.yml` runs on pushes to `main`, on every pull request, and by hand
(`workflow_dispatch`). Runners are pinned to `ubuntu-24.04`, actions are the Node 24 versions.

| Job | What it does |
|---|---|
| `backend` | PostgreSQL 16 service; installs the 16 client tools; `ruff check`; `pytest` with coverage, failing under 80%; `pip-audit` (blocking, with six advisories in pyATS-pinned packages ignored, see section 12). |
| `frontend` | `npm ci` and `npm run build` (TypeScript strict). |
| `image` | After backend and frontend: `docker build`, then Trivy (pinned image by digest) fails on fixed HIGH or CRITICAL findings, using `.trivyignore`. |
| `e2e` | Builds the UI, starts the API with the mock collector and thread jobs, seeds `t1` and `t2`, then the Playwright smoke test over 32 pages. |
| `proxy` | Validates the Caddyfile for all three TLS modes, and `docker compose config`. |
| `helm` | `helm lint --strict`, renders default and full configurations, validates with kubeconform against Kubernetes 1.30. |

## 8. Security model

- **Access:** local users (Argon2, minimum 12 characters, lockout after `MAX_FAILED_LOGINS`) and/or
  OIDC. Roles viewer < operator < admin, enforced per route in `api/deps.py` (`require(role)`). SSO
  maps IdP groups to roles on every sign-in and never takes over a local account of the same name.
- **Tokens:** session and API tokens are 256-bit random values stored as SHA-256 hashes, expiring
  and revocable. A token carries its user's role.
- **Device credentials:** each profile has a random data key encrypted by the master key
  (AES-256-GCM); ciphertext is bound to profile and field. Secrets are write-only over the API and
  never logged or audited. The master key lives outside the database (file or secret).
- **Device trust:** SSH host keys pinned per device (TOFU by default). A changed key fails the
  collection before credentials are sent, is audited, and needs an admin to reset the pin. The
  login session is bound to the pinned key through a one-off `known_hosts` file.
- **Collected data:** secrets are masked in every output (not only the running config) before
  parsing or storage. Only the fixed `show` commands in `commands.py` are run; VRF names are validated before they are put
  into a command.
- **Web:** strict CSP, `X-Frame-Options: DENY`, `nosniff`; request size cap; rate limits on login
  and the API, shared through Redis; `X-Forwarded-For` honoured only from `TRUSTED_PROXIES`;
  webhooks to private addresses refused unless allow-listed; user regexes bounded by
  `CONFIG_SEARCH_TIMEOUT`.
- **Audit:** every change to inventory, credentials, schedules, rules, users and tokens, and every
  snapshot request, goes to `audit_log`.
- **Containers:** non-root, read-only, image scanned on every CI run.
- **Recommended device account:** read-only, TACACS+ command authorization limited to `show`.

## 9. Data model

| Table | Holds |
|---|---|
| `users`, `api_tokens`, `oidc_states` | Accounts, hashed tokens, short-lived SSO state |
| `audit_log` | Who did what, when, from where |
| `credential_profiles` | Username plus encrypted secrets and data key |
| `sites`, `devices` | Inventory; a device has platform, host, port, site, role, tags, status, optional credential, pinned host key |
| `snapshots` | One run: scope, labels, trigger, status, timestamps, `summary` JSON (row counts, change summary vs baseline, check summary) |
| `device_collections` | One device in one snapshot: status, error, warnings, `failed_tables`, `device_meta` (platform, site, role at the time) |
| `state_rows` | The normalized data: (snapshot, device, table, key) → JSON `data` and content hash. Most of the database (about 780 bytes per row) |
| `raw_outputs` | Pointers to raw text and Genie JSON in the object store, per device and command |
| `check_rules` | Custom rules and enable/disable overrides for built-ins |
| `check_results` | Violations per snapshot and rule |
| `check_profiles` | Rules, label prefix, route drop percent, strict |
| `schedules`, `report_schedules` | Cron schedules for snapshots and e-mailed reports |
| `discovery_ignores` | Neighbors hidden from discovery |

Normalized tables (`services/diff.py` `TABLES`): `facts`, `interfaces`, `routes`, `arp`,
`neighbors`, `bgp`, `ospf`, `mac`, `vlans`, `vrfs`, `hardware`, `portchannels`, `stp`, `fhrp`,
`ntp`, `snmp`, `aaa`, `config`.

Sizing: hourly snapshots of 500 devices with 7-day thinning and 90-day retention need about 100 GB
of database.

## 10. How to extend it

### Add a command and parser to an existing table

1. `collection/commands.py`: add the command under `COMMANDS[IOS_XE]` and/or `COMMANDS[NX_OS]`,
   keyed by a source name. It must have a Genie parser for that OS. For IOS-XE commands that need
   one run per VRF, add to `PER_VRF_COMMANDS`.
2. `collection/normalize.py`: read `genie.get("<source>")` in `normalize()` (or in the table's
   normalizer) and merge it into the table. Drop volatile fields (counters, timers, ages) so they
   never show as changes. Canonicalize interface names with `expand_ifname`.
3. If the source feeds a table with a different name, add it to `SOURCE_TABLES` so a failed
   command marks the right tables unknown.
4. Add mock output in `mock_data/t1/<device>/` and `t2/` named by `command_filename(cmd)` (for
   example `show_ip_route.txt`), and a test.

### Add a new table

1. Steps 1 to 3 above, plus a normalizer function returning `{key: row}`. Pick a key that is stable
   across snapshots and unique per device (keys over 512 characters are cut and given a hash suffix). Register it in
   `TECH_NORMALIZERS` or the dict in `normalize()`.
2. Add the table name to `TABLES` in `services/diff.py`. This makes it diffable, available at
   `/snapshots/{ref}/tables/{table}` and to custom `row` rules.
3. UI: add a `TableDef` in `frontend/src/tables.tsx` (id, title, group, columns). Navigation, the
   snapshot and device tabs, and Compare pick it up from there. Add a one-line summary to `brief()`
   in `pages/Compare.tsx` if useful.
4. Tests: normalizer test with real-looking Genie output, plus a diff test. No migration is needed;
   rows are JSON.

### Add a built-in check

1. `services/checks.py`: write a generator `def _my_rule(s: SnapshotData, **_)` (state) or
   `def _my_rule(s, prev, **_)` (delta) that yields `Violation(device, key, message, data)`. Use
   `s.rows(table)` (it skips failed devices and tables) and, for delta rules, `prev.ok(d, table)
   and s.ok(d, table)` before treating a missing row as lost.
2. Add a `Rule(id, name, description, severity, "state"|"delta", tables, fn)` to `BUILTIN`.
3. Add it to a profile template in `frontend/src/pages/Checks.tsx` (`TEMPLATES`) if it belongs to
   one.
4. Tests in `tests/test_beta.py` or `tests/test_logic.py`, including a case where the device or
   command failed (no false alarm).

### Schema changes

Change `models.py`, then add a migration under `assurance/migrations/versions/` (next is `0011`).
`tests/test_migrations.py` fails if migrations and models disagree. Large indexes on `state_rows`
must be built `CONCURRENTLY` (see 0008 and 0009).

## 11. Testing

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -c constraints.txt -e '.[dev]'
export TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/assurance_test
pytest                      # about 380 tests; needs PostgreSQL; uses mock_data and the filesystem store
ruff check assurance tests
cd frontend && npm ci && npm run build
```

| File | Covers |
|---|---|
| `test_collection.py` | Genie parsing and normalization of the mock captures |
| `test_logic.py` | Normalizers against Cisco's own Genie golden outputs (235 samples), property tests with Hypothesis, diff/check/baseline scenarios from the PR 5 logic review |
| `test_api.py`, `test_beta.py`, `test_review.py` | API end to end: inventory, snapshots, diffs, checks, pre/post, topology, reports, search, discovery |
| `test_security.py`, `test_hardening.py`, `test_hostkeys.py`, `test_oidc.py` | Crypto, auth, roles, rate limits, headers, host key pinning, SSO against a fake IdP |
| `test_report_mail.py` | Scheduled reports against a fake SMTP server |
| `test_backup.py`, `test_migrations.py`, `test_cli.py`, `test_tracing.py` | Backup/restore, migrations vs models, CLI, tracing |

### Mock mode

Run without devices by setting `ASSURANCE_COLLECTOR=mock`. The mock collector reads
`<ASSURANCE_MOCK_ROOT>/<scenario>/<device>/<command file>.txt`. A missing file means empty output
(feature not in use); a file named `<command file>.txt.error` makes that command fail like a
timeout. Snapshots choose the scenario with `mock_scenario` (API) or the hidden
`--mock-scenario` CLI option; the API rejects it on a live server.

```bash
ASSURANCE_COLLECTOR=mock ASSURANCE_JOB_BACKEND=thread \
  uvicorn --factory assurance.api.app:create_app --reload
assurance snapshot --mock-scenario t1
assurance snapshot --mock-scenario t2
assurance diff
```

`t2` differs from `t1` by a link down on rtr01 Gi3 (routes, ARP and a CDP neighbor lost), a new BGP
prefix, a description change, an NX-OS upgrade, a new static route, an ARP MAC move and an ESXi
host swap.

`tools/loadtest/synth.py` builds a synthetic fleet (for example 1,000 devices) from the captures,
and `tools/loadtest/run.py` measures a running deployment against it. Results from 18 November 2026
are in [loadtest-2026-11-18.md](loadtest-2026-11-18.md): 128 devices per minute on 3 workers, the
dashboard in 0.09 s, a fleet diff in 0.7 s.

`tools/e2e/seed.py` and `tools/e2e/smoke.cjs` are the browser smoke test that CI runs.

## 12. Known gaps and risks

Read these before putting Assurance in front of production users.

**Never run against a live device.** All collection has been tested with hand-written mock
captures (checked with Genie) and Cisco's Genie golden outputs. The live path (`LiveCollector`:
pyATS/Unicon SSH, enable handling, prompts, timeouts, host key binding) has not connected to a real
IOS-XE or NX-OS box. Before go-live: run one lab or low-risk device of each platform, compare every
table against the CLI by hand, take two snapshots with no change and confirm the diff is empty, then
make a known change and confirm it shows.

**SSO and e-mail only tested against fakes.** OIDC is tested against a fake identity provider and
scheduled reports against a fake SMTP server. Test with the real IdP (redirect URI, groups claim,
role map, token signing keys) and the real mail relay (STARTTLS or SSL, auth, attachment size).

**Open logic items from the PR 5 review:**

- Truncated output is not detected. If a session dies mid-command but returns partial text, the
  rows that are missing look removed.
- IPv6 interface addresses and the IPv6 neighbor (ND) table are not collected; IPv6 is covered for
  routes only.
- OSPF on IOS-XE is not split by VRF.
- First diff after upgrading to the PR 5 code shows one-time changes (canonical interface names,
  new IPv6 and per-VRF rows, NTP and SNMP keys). Warn users before the first diff across the
  upgrade.

**Other known gaps:**

- No VRRP on NX-OS (Genie has no parser).
- Non-Cisco LLDP neighbors can show title-cased port names from Genie; common lower-case forms
  (`ge-0/0/0`, `vmnic0`) are restored, others may not be.
- No reachability check when a device is added.
- Topology is one level (no site grouping).
- Helm chart never installed on a cluster; no failover test of the scheduler leader lock, Redis or
  PostgreSQL; no outside penetration test.
- PR 6 (hide **Lab options** in the UI on live servers, add `GET /api/v1/system/info`) may still be
  open. Until it merges, the New snapshot form shows a mock option that the API rejects on live
  servers.

**Accepted Trivy advisories (`.trivyignore`).** The image scan ignores five CVEs, all in packages
pyATS pins:

| CVE | Package | Why accepted |
|---|---|---|
| CVE-2026-48802, CVE-2026-48809, CVE-2026-48804 | python-engineio 4.9.1 / python-socketio 5.11.2 | pyATS 26.10 pins them for its log viewer. Assurance never starts a Socket.IO server, so the DoS paths are unreachable. |
| CVE-2026-23949, CVE-2026-24049 | jaraco.context 5.3.0, wheel 0.45.1 vendored in setuptools 79.0.1 | pyATS pins setuptools below 80. Only used when building or unpacking packages, never at runtime. |

`pip-audit` in the backend job ignores six matching Python advisories for the same reasons
(`PYSEC-2026-1854`, `PYSEC-2026-3042`, `PYSEC-2026-3032`, `PYSEC-2026-3033`, `PYSEC-2026-3447`,
`GHSA-xmj3-9gf7-h52w`). Review both lists on every release and drop each ignore once pyATS lifts its
pin.

**Operational risks:**

- The master key is the single point of failure for device credentials. Back it up separately from
  the database backups.
- Database growth is dominated by `state_rows`. Keep retention and thinning on; watch disk.
- Schedules with labels other than `schedule:...` produce snapshots that retention never deletes.
- Genie parser behaviour changes between pyATS releases. pyATS is pinned per major and versions are
  pinned in `constraints.txt`; re-run `tests/test_logic.py` before any upgrade.

## 13. Where to start

1. Read this document, then `README.md`.
2. Run the test suite and the UI locally in mock mode (section 11).
3. Read `services/snapshots.py`, `collection/pipeline.py`, `services/diff.py` and
   `services/checks.py`; they hold almost all of the logic.
4. Close the first gap in section 12: a live run against lab devices.
