# Assurance user guide

This guide is for network engineers who use Assurance day to day. It explains how to get devices
in, take snapshots, see what changed, verify a change, and pull the data out through reports, the
API and the CLI.

Labels in **bold** are the exact names you see in the web UI. Everything you can do in the UI you
can also do through the API.

- [What Assurance does](#what-assurance-does)
- [Signing in](#signing-in)
- [Roles](#roles)
- [Finding your way around](#finding-your-way-around)
- [Inventory](#inventory)
- [Device credentials](#device-credentials)
- [Snapshots](#snapshots)
- [Retention: how long snapshots are kept](#retention-how-long-snapshots-are-kept)
- [Changes (Compare)](#changes-compare)
- [Change labels and pre/post verification](#change-labels-and-prepost-verification)
- [Intent checks](#intent-checks)
- [Check profiles](#check-profiles)
- [Topology](#topology)
- [Search](#search)
- [Discovery](#discovery)
- [Reports](#reports)
- [The API and API tokens](#the-api-and-api-tokens)
- [The CLI](#the-cli)
- [Troubleshooting](#troubleshooting)

## What Assurance does

Assurance logs in to your Cisco IOS-XE and NX-OS devices over SSH, runs a fixed set of read-only
`show` commands, parses the output with Cisco Genie, and stores the result as a **snapshot**. Every
snapshot is kept as history, so you can compare any two points in time and see, row by row, what
was added, removed or modified.

Each snapshot holds these tables per device:

| Table | Source |
|---|---|
| Device facts | `show version` (hostname, model, serial, OS version) |
| Hardware | `show inventory` |
| Interfaces | `show interfaces` / `show interface` |
| Routes | `show ip route`, `show ipv6 route` (every VRF) |
| ARP | `show ip arp` (every VRF) |
| Neighbors | `show cdp neighbors detail`, `show lldp neighbors detail` |
| BGP | `show bgp all summary` / `show bgp vrf all all summary` |
| OSPF | `show ip ospf neighbor` |
| MAC | `show mac address-table` |
| VLANs, VRFs | `show vlan`, `show vrf` |
| Port-channels | `show etherchannel summary` / `show port-channel summary` |
| Spanning tree | `show spanning-tree detail` |
| HSRP / VRRP | `show standby all`, `show vrrp` / `show hsrp all` (no VRRP on NX-OS) |
| NTP | `show ntp associations` / `show ntp peer-status` |
| SNMP, AAA, Config | `show running-config`, with secrets masked before anything is stored |

Counters, ages and uptimes are dropped before comparison, so they never show up as changes.

## Signing in

Open the Assurance address your administrator gave you (for example
`https://assurance.example.net`) and sign in.

- **Local account:** enter your **Username** and **Password** and select **Sign in**.
- **Single sign-on:** if your company's identity provider is set up, select **Sign in with
  &lt;name&gt;** (the name is set by your administrator, by default **Single sign-on**). Your role
  comes from your groups at the identity provider and is refreshed every time you sign in. If you
  are not in a mapped group, you may be refused; ask your administrator to add you.

After 5 wrong passwords in a row the account is locked for 15 minutes (both numbers can be changed
by the administrator). A session lasts 12 hours.

To change your password, open the account menu (your initials, top right) and choose **Change
password**. The same menu has **API reference** and **Sign out**. The moon or sun icon next to it
(**Dark mode** / **Light mode**) switches the theme.

## Roles

| Role | Can do |
|---|---|
| viewer | See everything except credentials, users and the audit log. Run change verification, search and reports. Create their own API tokens. |
| operator | Everything a viewer can, plus start snapshots (**New snapshot**, **Collect now**), **Re-grade latest**, and test a rule (**Test against latest**). |
| admin | Everything, including inventory changes, CSV import, credentials, schedules, intent rules, check profiles, discovery approval, scheduled reports, users and the audit log. |

## Finding your way around

The left menu is grouped:

- **Overview:** **Dashboard** and **Snapshots**.
- **Inventory:** **Devices**, **Device facts**, **Hardware**, **Discovery**.
- **Technology tables** (Interfaces, Routing, Switching and so on): one page per table, with column
  filters, search and CSV export.
- **Analysis:** **Changes** (the compare view), **Intent checks**, **Change verification**,
  **Topology**, **Search**, **Reports**.
- **Administration:** **Settings**.

Two controls at the top are on every page:

- **Snapshot** picker. Tables, device pages and the topology show data from the snapshot chosen
  here. **Always the latest** follows new snapshots as they finish; pick an older snapshot to look
  at the network as it was then.
- **Search** box. Type an IP, MAC, prefix, hostname or serial and press Enter. Press `/` anywhere to
  jump to it.

## Inventory

Assurance keeps its own inventory. Nothing is pulled from another system.

### Add one device

**Devices → Add device** (admin). Fill in:

| Field | Notes |
|---|---|
| **Name** | The inventory name. Cannot be changed later. |
| **Management IP / host** | Address Assurance connects to over SSH. |
| **Platform** | **IOS-XE** or **NX-OS**. |
| **SSH port** | 22 by default. |
| **Site** | Typed in; a new site is created if it does not exist. |
| **Role** | Free text, for example `core-router`. Used for scoping snapshots and rules. |
| **Tags** | Comma separated, for example `core, dc`. |
| **Credential profile** | Leave on **Inherit (site, then "default")** unless the device needs its own account. |

To edit a device later, open it and select **Edit**. The **Status** field there can be **active**,
**maintenance** or **retired**. Only active devices are collected, so set a device to
**maintenance** to leave it out of snapshots for a while.

### Import from CSV

**Devices → Import CSV** (admin).

1. Prepare a CSV file. `name`, `host` and `platform` are required; `site`, `role`, `tags`, `port`,
   `status` and `credential` are optional. Separate several tags with `;`. `platform` is `iosxe`
   or `nxos`.

   ```csv
   name,host,platform,site,role,tags
   yyc-core-rtr01,10.10.0.1,iosxe,YYC-DC1,core-router,core
   yyc-dc-nxs01,10.10.0.11,nxos,YYC-DC1,dc-spine,dc;spine
   ```

2. Choose the file and select **Preview**. Nothing is written yet. You see how many devices are
   **new**, **updated** and unchanged, any new sites, and a table of rows with problems (line
   number, name and the problem).
3. Fix any problems in the file, then select **Apply import**.

Devices are matched by name, so importing the same file again only updates what changed.
**Export** on the same page downloads the inventory as CSV in the same format.

### Device page

Select a device to open its page. Tabs show its **Overview**, every table for that device,
**History** (changes over time) and **Configuration** (the running config with secrets masked).
Buttons: **Collect now** (operator; takes a one-device snapshot), **Changes**, **Edit** and
**Remove** (admin).

The **Device** card shows the pinned **SSH host key**. Assurance remembers each device's SSH host
key the first time it connects. If the key later changes (for example after an RMA or a reload
that regenerated keys), collection of that device fails before any password is sent. When you
know the change is legitimate, an admin selects **reset pin**, and the next collection pins the new
key. Check the new fingerprint afterwards.

## Device credentials

**Settings → Device credentials** (admin).

Add a profile with **Profile name**, **Username**, **Password** and, if needed, **Enable secret
(optional)**, then select **Add profile**. Use a read-only account limited to `show` commands
(TACACS+ command authorization or a low privilege level).

- Passwords are encrypted as soon as you save them and are never shown again, not even to admins.
  In the UI, change one by deleting the profile and adding it again with the same name; the API
  can update it in place (`PATCH /api/v1/credentials/{name}`).
- Which profile a device uses: the profile set on the device, otherwise the site's profile,
  otherwise the profile named `default`. Create a `default` profile first; live collection needs at
  least one profile.
- A site's profile is set through the API (`PATCH /api/v1/sites/{name}` with `credential`).

## Snapshots

A snapshot is one collection run over a set of devices. Snapshots never change once they finish.

### Take a snapshot now

**Snapshots → New snapshot** (operator).

- Leave every scope field empty to collect every active device.
- Or narrow it with **Site**, **Platform**, **Role**, **Tags (all must match)** or **Devices**
  (comma separated names).
- **Labels:** optional tags for the snapshot, for example `change:CHG0012345`. See
  [Change labels](#change-labels-and-prepost-verification).
- Select **Start snapshot**. You are taken to the snapshot page, which updates while devices are
  collected.

On a lab or demo server that runs the mock collector, the form also has **Lab options → Mock
scenario**. Live servers do not show it.

From a device page, **Collect now** takes a snapshot of just that device.

### Statuses

| Snapshot status | Meaning |
|---|---|
| queued, running, finalizing | Still in progress. |
| complete | Every device collected. |
| partial | Some devices failed, or some commands failed on a device. The rest is usable. |
| failed | No device could be collected. Failed snapshots cannot be compared. |

On the snapshot page, the **Devices** tab lists each device with its status (**ok**, **partial**,
**failed**), time, rows and **Notes** (errors and parser warnings). **Raw output** shows the exact
CLI text the device returned. **Diff vs previous** opens the changes since the previous snapshot of
the same devices.

On the **Snapshots** list, the **Changes** column counts changes against the previous snapshot and
links to them, and **Checks** shows failing and warning intent check counts. Tick two snapshots
and select **Compare selected** to compare them. An admin can delete a snapshot with the bin icon.

### Snapshots on a schedule

**Settings → Collection schedules**. Admins add a schedule with a **Name** (for example
`hourly-fleet`), a **Cron** expression, an optional **Site** and optional **Labels**, then select
**Add schedule**.

- Cron times are **UTC**. `0 * * * *` is every hour on the hour; `0 2 * * *` is 02:00 UTC daily.
- The **Next run** and **Last run** columns show when it runs. Untick **Enabled** to pause it.
- Every scheduled snapshot is labelled `schedule:<name>` automatically.
- Do not add other labels to a schedule unless you want those snapshots kept for ever: any label
  other than `schedule:...` marks a snapshot as change evidence, and retention never deletes it.

Viewers and operators can see schedules but not change them.

## Retention: how long snapshots are kept

Retention is set by your administrator (see the handoff document). The usual settings are:

- Routine snapshots older than 90 days are deleted.
- Routine snapshots older than 7 days are thinned to the last one of each UTC day.

Two kinds of snapshot are **always kept**:

- Any snapshot with a label other than `schedule:...` (for example `change:CHG0012345`). This is
  how change evidence survives.
- The newest complete snapshot, so there is always a baseline for the next one.

If you need an old routine snapshot kept, there is no button for it yet; ask an admin before it
ages out.

## Changes (Compare)

**Analysis → Changes** shows what changed between two points in time.

1. Pick a preset (**Previous → latest**, **Last 24 hours**, **Last 7 days**) or choose snapshots in
   **From** and **To**. A time like `24h` means the last snapshot taken at or before 24 hours ago.
2. Read the totals: **Total changes**, **Added**, **Removed**, **Modified** and **Not compared**.
3. Narrow down with the left panel: **Change type**, **Table** (several can be selected) and
   **Device**. **Search key or value** filters the list further.
4. **Export CSV** downloads every change that matches the filters.

Each change shows the device, table and key. A modified row lists each field that changed with the
old value struck through and the new one after it. Config changes show the changed lines in red and
green.

Things to know:

- **Not compared** devices failed collection in one of the two snapshots. Assurance does not report
  their rows as removed, because it does not know. A yellow banner lists them.
- If a single command failed on a device (a timeout, or TACACS refused it), only the tables that
  command feeds are skipped for that device, and a banner says **Not compared because a command
  failed in one of the snapshots**.
- **Only in the later snapshot / Only in the earlier snapshot** lists devices that are in one
  snapshot's scope but not the other's.
- **Previous** means the previous snapshot that covers the same devices, not simply the last one
  taken. A one-device **Collect now** does not become the baseline for the next fleet snapshot.

For one device, its **History** tab shows a change timeline; **Open in Changes** jumps to the full
view.

## Change labels and pre/post verification

A change label ties the snapshots of one change together. Use the form `change:<ticket>`, for
example `change:CHG0012345`.

**Use the same label before and after the change.** Assurance takes the first snapshot with the
label as the "before" and the last one as the "after".

### Step by step

1. Before the change window, open **Snapshots → New snapshot**, set the scope to the devices you
   will touch (or leave it empty), and put `change:CHG0012345` in **Labels**. Select **Start
   snapshot** and wait for it to finish.
2. Make the change.
3. Take another snapshot with the same scope and the **same label** `change:CHG0012345`.
4. Open **Analysis → Change verification**, select **By change label**, choose
   `change:CHG0012345` from **Label**, and select **Verify**.

You can also use **Pick snapshots** and choose **Before** and **After** yourself.

Optional settings on the same page:

- **Check profile:** by default **Auto (from change label), else all checks**. See
  [Check profiles](#check-profiles).
- **Route drop threshold:** overrides the profile's threshold for this run, in percent.

### Reading the result

The verdict is one of:

- **Change verified** (pass)
- **Verified with warnings**
- **Verification failed**

It fails when any of these is true:

- A BGP session that was Established is down or gone.
- An OSPF adjacency that was FULL is down or gone.
- An interface that was up is down or gone.
- A bundled port-channel member left its bundle.
- A spanning-tree root moved.
- Routes in a VRF on a device dropped by more than the threshold (2% by default).
- A device collected before the change was not collected after (**Not collected after**).
- A state check that passed before now fails (shown as **New: &lt;rule name&gt;**).

Lost CDP/LLDP neighbors and HSRP/VRRP role changes are warnings. With a strict profile, warnings
fail the verification too.

Below the verdict, **Checks** lists every check with its result and number of findings, and each
failing check has a table of findings. **Full diff** opens every change between the two
snapshots.

Labelled snapshots are kept by retention, so the evidence stays available.

## Intent checks

**Analysis → Intent checks**. Every snapshot is graded against a set of rules when it finishes.

- **Compliance** is the share of enabled rules that pass on the latest snapshot.
- **Rules** lists each rule with its status, severity, **Source** (**built-in** or **custom**) and
  number of violations. Click a rule to filter the **Violations** table to it.
- **Violations over time** charts failing and warning counts.
- **Re-grade latest** (operator) grades the latest snapshot again, for example after you change a
  rule.

### Built-in rules

State rules (graded on every snapshot):

| Id | What it checks | Severity |
|---|---|---|
| `bgp-established` | Every BGP neighbor is Established (neighbors shut down on purpose are skipped). | fail |
| `ospf-full` | Every OSPF neighbor is FULL (or 2WAY between DROTHERs). | fail |
| `interface-up-down` | Admin-up interfaces are operationally up. Descriptions containing spare, unused, reserved or shut are ignored. | warning |
| `link-mtu-mismatch` | Both ends of a CDP/LLDP link between collected devices have the same MTU. | fail |
| `link-duplex-mismatch` | Both ends of a link have the same duplex. | fail |
| `config-no-http-server` | No `ip http server` or `feature telnet`. | fail |
| `config-ntp` | At least one `ntp server` line. | fail |
| `config-ssh-v2` | IOS-XE has `ip ssh version 2`. | warning |
| `config-aaa` | `aaa new-model` (IOS-XE) or `feature tacacs+` (NX-OS). | warning |
| `collection-ok` | Every device in scope was collected without errors. | warning |
| `portchannel-up` | Every port-channel is up. | fail |
| `portchannel-members-bundled` | Every configured member is bundled. | warning |
| `ntp-synchronized` | Devices with NTP peers are synchronized. | fail |
| `snmp-no-rw-community` | No read-write SNMP community. | fail |
| `snmp-community-acl` | Every SNMP community has an access list. | warning |
| `fhrp-redundant` | HSRP/VRRP groups are active or standby, and HSRP groups have a standby router. | warning |

Pre/post rules (used only by change verification; their status shows **pre/post**):
`bgp-neighbor-lost`, `ospf-neighbor-lost`, `interface-went-down`, `route-count-drop`,
`neighbor-lost`, `stp-root-changed`, `fhrp-state-changed`, `portchannel-member-lost`.

### Built-in rules can only be turned on or off

An admin can **Disable** or **Enable** a built-in rule. Its logic, scope and severity cannot be
edited. To change how one behaves, disable it and create a custom rule that does what you want.
For example, to require SSH v2 on NX-OS as well and make it a failure instead of a warning:

1. **Disable** `config-ssh-v2`.
2. Select **New rule** and create a custom `config_require` rule (example below) with **Severity**
   **Fail**.

### Custom rules

**Intent checks → New rule** (admin). Fill in **Name (id)** (it becomes `custom:<name>`),
**Severity** (**Fail** or **Warning**), **Title**, **Description**, and the **Definition (YAML or
JSON)**. Select **Test against latest** to see what it would flag on the latest snapshot before you
**Save**. Saving re-grades the latest snapshot. Custom rules have an **Edit** button; in the editor
you can also **Delete** them.

There are three kinds.

**`row`**: filter rows of a table, then assert something about each row that matches.

```yaml
# Every NX-OS server port must run MTU 9216
kind: row
table: interfaces
where:
  - {field: description, op: regex, value: "^to .*esx"}
assert: {field: mtu, op: eq, value: 9216}
scope: {platform: nxos}
message: "{key} MTU is {mtu}, expected 9216"
```

**`config_require`**: every device in scope must have a config line matching the pattern.

```yaml
# SSH v2 everywhere (custom copy of config-ssh-v2, for both platforms)
kind: config_require
pattern: '^(ip )?ssh version 2$|^feature ssh$'
message: "SSH version 2 is not configured"
```

**`config_forbid`**: no device in scope may have a config line matching the pattern.

```yaml
kind: config_forbid
pattern: '^snmp-server community .* RW'
scope: {site: YYC-DC1}
```

Reference:

- `table`: one of `facts`, `interfaces`, `routes`, `arp`, `neighbors`, `bgp`, `ospf`, `mac`,
  `vlans`, `vrfs`, `hardware`, `portchannels`, `stp`, `fhrp`, `ntp`, `snmp`, `aaa`, `config`. Field
  names are the column names you see on the table pages (use the API or **Export** to see the raw
  field names).
- `op`: `eq`, `ne`, `in`, `not_in`, `regex`, `not_regex`, `gt`, `gte`, `lt`, `lte`, `exists`,
  `not_exists`, `contains`.
- `scope`: any of `platform` (`iosxe` or `nxos`), `site`, `role`, `devices` (a list of names).
- `message`: optional. `{key}` and any field name in braces are filled in from the row.
- A `where` condition that cannot be evaluated on a row (for example `gt` on text) just does not
  match that row.

## Check profiles

A check profile names the checks a type of change must pass. It is shown under **Intent checks →
Check profiles**; admins select **New profile** or **Edit**.

| Field | Meaning |
|---|---|
| **Name** | For example `routing-change`. |
| **Change label prefix** | For example `change:rtg-`. When you verify a change whose label starts with this, the profile is picked automatically. If several match, the longest prefix wins. |
| **Description** | Free text. |
| **Route drop threshold (%)** | Default **2**. See below. |
| **Strict** | **Strict: warnings fail the verification too.** |
| Checks | Tick the checks to run. None ticked means all active checks. **Start from:** **Routing change**, **Switching / L2 change** or **Firmware upgrade** pre-fills a set. |

**Route drop threshold** is applied **per device and per VRF**. For each device and VRF, Assurance
counts routes before and after; if the count dropped by more than the threshold percentage,
`route-count-drop` fails for that device and VRF. With the default 2%, a VRF that had 1,000 routes
fails at 979 or fewer. A VRF that disappears entirely counts as a 100% drop. The threshold on the
**Change verification** page, the `route_drop_pct` API field and the CLI's `--route-drop-pct`
override the profile's value for one run.

Without any profile, change verification runs every active check with a 2% threshold and no strict
mode.

How a profile is chosen when you do not pick one: in **By change label** mode, from that label; in
**Pick snapshots** mode, from the first label on the "after" snapshot.

## Topology

**Analysis → Topology** draws the network from CDP and LLDP neighbors in the selected snapshot.

- Device colours: healthy, warnings, failing checks, and discovered (not in inventory).
- **Highlight changes since** (**Previous snapshot**, **24 hours ago**, **7 days ago** or a chosen
  snapshot) colours links that were added or removed since then.
- **Site** limits the map to one site.
- **Show discovered (unmanaged) neighbors** adds neighbors that are not in inventory.
- Click a device to open its page; scroll to zoom. The **Links** table below lists every link
  with **Device A**, **Interface A**, **Device B**, **Interface B**, **Learned by** and **Change**.

Neighbors are matched to inventory by hostname too, so `YYC-CORE-RTR01.corp.example` and
`yyc-core-rtr01` are the same node.

## Search

**Analysis → Search** (or the search box at the top).

- **Latest snapshot:** where an IP, MAC, prefix, hostname, serial or text appears now. For an IP it
  also shows the route used to reach it (longest prefix match).
- **History:** in which snapshots it was seen over the last 24 hours, 7, 30 or 90 days.
- **Configs:** which config lines match, across all devices. Tick **Regular expression** for a
  regex.

## Discovery

**Inventory → Discovery** lists CDP/LLDP neighbors that are not in inventory yet. Nothing is added
without approval. An admin ticks candidates (only supported platforms with a management IP can be
ticked), optionally sets **Site** and **Role**, and selects **Add N to inventory**. Ticking
**Collect them now (next crawl step)** collects the new devices straight away, which can find more
neighbors. **Ignore** hides a candidate; **Show ignored** and **Un-ignore** bring it back.

## Reports

**Analysis → Reports**.

| Report | Contents |
|---|---|
| **Change report** | Every added, removed and modified row between two snapshots, by device and table. Uses **From (change report)** and **Snapshot**. |
| **Compliance report** | Intent check results: rule status, every violation, and a per-device score. |
| **Inventory report** | Devices, models, OS versions, serial numbers and hardware modules. |

**Open** shows the HTML report in a new tab (use the browser's print to save it as PDF).
**Excel** and **CSV** download the file.

**Scheduled reports** (admin) emails a report as an attachment: give it a name, the report type,
format (**Excel**, **HTML** or **CSV**), for the change report a window (**last 24 h**, **last 7
days**, **last 30 days**), a UTC cron and the recipients, then select **Schedule**. **Send now**
sends it immediately; **Pause**, **Resume** and **Delete** manage it. Email needs the server's SMTP
settings.

## The API and API tokens

Everything is available over a REST API under `/api/v1`. The interactive reference is at
`/api/docs` on your server (also in the account menu as **API reference**).

### Create a token

**Settings → API tokens**. Type a **Token name** (for example `change-pipeline`) and select
**Create token**. The token is shown **once**; copy it then. Tokens from the UI expire after 90
days. **Revoke** stops a token immediately. A token has the same role as the user who created it.

Send it on every request:

```bash
TOKEN=...   # from Settings -> API tokens
curl -H "Authorization: Bearer $TOKEN" https://assurance.example.net/api/v1/snapshots?limit=5
```

### Common calls

```bash
API=https://assurance.example.net/api/v1
H="Authorization: Bearer $TOKEN"

# Start a snapshot of one site with a change label (returns 202 and the snapshot id)
curl -X POST -H "$H" -H 'Content-Type: application/json' "$API/snapshots" \
  -d '{"scope": {"site": "YYC-DC1"}, "labels": ["change:CHG0012345"]}'

# Status of a snapshot (id, latest, previous, an ISO time or an age like 24h)
curl -H "$H" "$API/snapshots/latest"

# What changed in the last 24 hours, routes only, as CSV
curl -H "$H" "$API/diffs?from=24h&to=now&table=routes&format=csv"

# One table from a snapshot, filtered by field
curl -H "$H" "$API/snapshots/latest/tables/bgp?state=Idle"

# Pre/post verification by change label
curl -X POST -H "$H" -H 'Content-Type: application/json' "$API/compare/prepost" \
  -d '{"label": "change:CHG0012345"}'
```

Errors come back as JSON with a `detail` explaining the problem. Requests are rate limited (600 per
minute per token by default); a `429` means slow down.

If you pass `callback_url` when starting a snapshot, Assurance POSTs a `snapshot.finished` event to
that URL when it completes, signed with an `X-Assurance-Signature` header. Ask your administrator
for the signing secret.

## The CLI

The `assurance` command is a client of the API, so it runs on any machine that can reach the
server. Install it with `pip install .` from the repository, or use the container image.

```bash
assurance login --url https://assurance.example.net     # prompts for username and password
assurance whoami
```

In pipelines, set `ASSURANCE_URL` and `ASSURANCE_TOKEN` instead of logging in.

| Command | What it does |
|---|---|
| `assurance devices list [--site S] [--role R] [--platform P] [--tag T]` | List devices. |
| `assurance devices add NAME HOST --platform iosxe` | Add a device. |
| `assurance devices import devices.csv [--apply]` | Dry run by default; `--apply` writes. |
| `assurance devices history NAME [--since 7d]` | Change timeline for a device. |
| `assurance credentials list` / `create NAME` | Credential profiles (admin). |
| `assurance snapshot [--site S] [-d DEVICE] [--label L] [--no-wait]` | Take a snapshot; prints the id and waits for it. |
| `assurance snapshots list [--label L]` / `show [REF]` | List or inspect snapshots. |
| `assurance snapshots table TABLE [REF] --device D -q TEXT` | Print a table. |
| `assurance diff [FROM] [TO] [-t TABLE] [-d DEVICE] [--json] [--fail-on-change]` | What changed. Defaults to previous → latest. |
| `assurance prepost --label L` or `--pre A --post B` `[--profile P] [--route-drop-pct N]` | Change verification. |
| `assurance checks rules` / `profiles` / `show [REF]` / `run [REF]` | Intent checks. |
| `assurance config show DEVICE` / `config diff DEVICE [OLD] [NEW]` | Masked running config and its unified diff. |
| `assurance report changes\|compliance\|inventory --format html\|csv\|xlsx -o FILE` | Download a report. |

Exit codes for scripts and pipelines:

| Code | Meaning |
|---|---|
| 0 | Success. |
| 3 | `diff --fail-on-change` found changes. |
| 4 | `snapshot` finished as failed. |
| 5 | `prepost` verification failed. |

A full pre/post change in a pipeline:

```bash
assurance snapshot --site YYC-DC1 --label change:CHG0012345     # before
# ... make the change ...
assurance snapshot --site YYC-DC1 --label change:CHG0012345     # after, same label
assurance prepost --label change:CHG0012345                      # exit 5 on FAIL
```

## Troubleshooting

| Symptom | What to check |
|---|---|
| A device shows **failed** with an authentication error | Its credential profile (device, then site, then `default`). |
| A device fails with a host key error | The device's SSH host key changed. Confirm why, then an admin uses **reset pin** on the device page. |
| A device is **partial** | Open the snapshot, **Notes** column: a command timed out, was refused by TACACS, or a parser failed. Those tables show as not compared rather than removed. |
| **Changes** shows nothing for a device you changed | Check the device is in both snapshots and not under **Not compared**. |
| Verification says a device was **Not collected after** | The "after" snapshot did not include it, or it failed. Take the after snapshot with the same scope as the before. |
| **By change label** lists no labels | A label appears there only once at least two finished snapshots carry it. |
| A custom rule flags nothing | Use **Test against latest**, and check the field names against the table (Export a table to see them). |
