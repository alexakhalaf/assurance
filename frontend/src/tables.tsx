import { Link } from "react-router-dom";
import { Col, fmtCell } from "./components/DataGrid";
import { Badge } from "./components/ui";
import { speed } from "./hooks";

/** Technology tables: navigation, titles and column definitions. Keys match the API's table names. */
export interface TableDef { id: string; title: string; icon: string; group: string; blurb: string; columns: Col[] }

const device: Col = {
  key: "device", label: "Device", width: 170,
  render: (r) => <Link to={`/devices/${encodeURIComponent(r.device)}`} onClick={(e) => e.stopPropagation()}>{r.device}</Link>,
  text: (r) => r.device,
};
const key = (label = "Key", hidden = true): Col => ({ key: "key", label, mono: true, hidden, text: (r) => r.key });
const status = (k: string, label: string, options: string[]): Col => ({
  key: k, label, filter: "select", options, render: (r) => (r[k] == null ? fmtCell(null) : <Badge status={String(r[k])} />), text: (r) => String(r[k] ?? ""),
});
const bool = (k: string, label: string): Col => ({ key: k, label, filter: "select", options: ["true", "false"], text: (r) => String(r[k] ?? "") });
const mono = (k: string, label: string, extra: Partial<Col> = {}): Col => ({ key: k, label, mono: true, ...extra });
const nexthops: Col = {
  key: "next_hops", label: "Next hops", sortable: false,
  render: (r) => <div className="list-cell">{(r.next_hops || []).map((h: any, i: number) => (
    <span key={i} className="mono">{[h.next_hop, h.interface].filter(Boolean).join(" via ")}{h.vrf && h.vrf !== r.vrf ? ` (${h.vrf})` : ""}</span>
  ))}{!(r.next_hops || []).length && <span className="faint">—</span>}</div>,
  text: (r) => (r.next_hops || []).map((h: any) => [h.next_hop, h.interface].filter(Boolean).join(" ")).join(", "),
};
const list = (k: string, label: string, extra: Partial<Col> = {}): Col => ({
  key: k, label, sortable: false, render: (r) => fmtCell(r[k]), text: (r) => (r[k] || []).join(", "), ...extra,
});

export const TABLE_DEFS: TableDef[] = [
  { id: "facts", title: "Device facts", icon: "facts", group: "Inventory", blurb: "Hostname, model, serial and software per device, from show version.",
    columns: [device, { key: "hostname", label: "Hostname" }, { key: "model", label: "Model" }, mono("serial", "Serial"),
      mono("os_version", "OS version"), mono("image", "Image", { hidden: true }), { key: "uptime", label: "Uptime", hidden: true }] },
  { id: "hardware", title: "Hardware", icon: "hardware", group: "Inventory", blurb: "Chassis, modules, supervisors, power supplies and fans.",
    columns: [device, { key: "name", label: "Name" }, { key: "description", label: "Description", wrap: true }, mono("pid", "PID"), mono("vid", "VID", { hidden: true }), mono("serial", "Serial")] },
  { id: "interfaces", title: "Interfaces", icon: "interfaces", group: "Interfaces", blurb: "Every interface with admin and operational state, addressing, speed and MTU.",
    columns: [device, mono("key", "Interface", { text: (r) => r.key }), status("admin_status", "Admin", ["up", "down"]), status("oper_status", "Oper", ["up", "down"]),
      { key: "description", label: "Description" }, list("ipv4", "IPv4", { mono: true }),
      { key: "speed_mbps", label: "Speed", num: true, render: (r) => speed(r.speed_mbps), text: (r) => speed(r.speed_mbps), filter: false },
      { key: "duplex", label: "Duplex", filter: "select", options: ["full", "half", "auto"] }, { key: "mtu", label: "MTU", num: true },
      { key: "bandwidth_kbps", label: "Bandwidth (kbps)", num: true, hidden: true }, mono("mac", "MAC", { hidden: true })] },
  { id: "portchannels", title: "Port-channels", icon: "portchannels", group: "Interfaces", blurb: "LACP and static bundles with member state.",
    columns: [device, mono("key", "Port-channel", { text: (r) => r.key }), { key: "protocol", label: "Protocol", filter: "select", options: ["lacp", "none", "pagp"] },
      status("oper_status", "Oper", ["up", "down"]), { key: "layer", label: "Layer" }, list("members_bundled", "Bundled", { mono: true }), list("members_down", "Not bundled", { mono: true })] },
  { id: "neighbors", title: "Neighbors (CDP / LLDP)", icon: "neighbors", group: "Interfaces", blurb: "Discovered neighbors on every port.",
    columns: [device, { key: "protocol", label: "Protocol", filter: "select", options: ["cdp", "lldp"] }, mono("local_interface", "Local interface"),
      { key: "remote_device", label: "Neighbor" }, mono("remote_interface", "Neighbor interface"), { key: "remote_platform", label: "Platform", wrap: true }, mono("mgmt_ip", "Mgmt IP"), key()] },
  { id: "routes", title: "Routing table", icon: "routes", group: "Routing", blurb: "IPv4 routes in every VRF with protocol and next hops.",
    columns: [device, { key: "vrf", label: "VRF" }, mono("prefix", "Prefix"),
      { key: "protocol", label: "Protocol", filter: "select", options: ["connected", "local", "static", "ospf", "bgp", "eigrp", "isis", "rip", "hsrp", "am"] },
      { key: "distance", label: "AD", num: true }, { key: "metric", label: "Metric", num: true }, nexthops, key()] },
  { id: "bgp", title: "BGP neighbors", icon: "bgp", group: "Routing", blurb: "BGP sessions per VRF and address family.",
    columns: [device, { key: "vrf", label: "VRF" }, mono("neighbor", "Neighbor"), { key: "address_family", label: "Address family" },
      { key: "remote_as", label: "Remote AS", num: true }, { key: "local_as", label: "Local AS", num: true, hidden: true },
      status("state", "State", ["established", "idle", "active", "connect", "opensent", "openconfirm"]), { key: "prefixes_received", label: "Prefixes rcvd", num: true }, key()] },
  { id: "ospf", title: "OSPF neighbors", icon: "ospf", group: "Routing", blurb: "OSPF adjacencies per interface.",
    columns: [device, { key: "vrf", label: "VRF" }, mono("interface", "Interface"), mono("neighbor_id", "Neighbor ID"), mono("address", "Address"),
      status("state", "State", ["full", "2way", "init", "exstart", "exchange", "loading", "down"]), { key: "area", label: "Area", hidden: true }, key()] },
  { id: "vrfs", title: "VRFs", icon: "vrfs", group: "Routing", blurb: "VRFs with route distinguisher and member interfaces.",
    columns: [device, { key: "name", label: "VRF" }, mono("rd", "RD"), status("state", "State", ["up", "down"]), list("interfaces", "Interfaces", { mono: true, wrap: true })] },
  { id: "fhrp", title: "Gateway redundancy", icon: "fhrp", group: "Routing", blurb: "HSRP and VRRP groups with roles and virtual IPs.",
    columns: [device, mono("interface", "Interface"), { key: "protocol", label: "Protocol", filter: "select", options: ["hsrp", "vrrp"] }, { key: "group", label: "Group", num: true },
      { key: "state", label: "State", filter: "select", options: ["active", "standby", "master", "backup", "listen", "init"] }, { key: "priority", label: "Priority", num: true },
      mono("virtual_ip", "Virtual IP"), mono("active_router", "Active"), mono("standby_router", "Standby")] },
  { id: "arp", title: "ARP table", icon: "arp", group: "Layer 2", blurb: "IP to MAC bindings per VRF.",
    columns: [device, { key: "vrf", label: "VRF" }, mono("ip", "IP address"), mono("mac", "MAC address"), mono("interface", "Interface")] },
  { id: "mac", title: "MAC address table", icon: "mac", group: "Layer 2", blurb: "Learned and static MAC addresses per VLAN.",
    columns: [device, { key: "vlan", label: "VLAN" }, mono("mac", "MAC address"), { key: "type", label: "Type", filter: "select", options: ["dynamic", "static"] }, list("interfaces", "Ports", { mono: true })] },
  { id: "vlans", title: "VLANs", icon: "vlans", group: "Layer 2", blurb: "Configured VLANs and their access ports.",
    columns: [device, { key: "vlan_id", label: "VLAN", num: true }, { key: "name", label: "Name" }, status("state", "State", ["active", "suspend", "shutdown"]), list("interfaces", "Ports", { mono: true, wrap: true })] },
  { id: "stp", title: "Spanning tree", icon: "stp", group: "Layer 2", blurb: "Root bridge and port roles per VLAN or MST instance.",
    columns: [device, mono("key", "Instance", { text: (r) => r.key }), { key: "mode", label: "Mode" }, bool("is_root", "Root here"), mono("root_address", "Root bridge"),
      { key: "root_priority", label: "Root prio", num: true }, mono("root_port", "Root port"), { key: "bridge_priority", label: "Bridge prio", num: true, hidden: true },
      { key: "ports", label: "Ports", sortable: false, wrap: true, render: (r) => <span className="mono">{Object.entries(r.ports || {}).map(([i, role]) => `${i} ${role}`).join(", ") || "—"}</span>,
        text: (r) => Object.entries(r.ports || {}).map(([i, role]) => `${i} ${role}`).join(", ") }] },
  { id: "ntp", title: "NTP", icon: "ntp", group: "Management", blurb: "NTP peers, stratum and synchronization.",
    columns: [device, mono("key", "Peer", { text: (r) => r.key }), { key: "vrf", label: "VRF" }, { key: "stratum", label: "Stratum", num: true }, { key: "mode", label: "Mode" }, bool("synchronized", "Synchronized")] },
  { id: "snmp", title: "SNMP", icon: "management", group: "Management", blurb: "Communities, users, hosts and views (secrets removed).",
    columns: [device, { key: "kind", label: "Kind", filter: "select", options: ["community", "user", "host", "group", "view", "location", "contact"] },
      { key: "name", label: "Name" }, { key: "access", label: "Access" }, mono("acl", "ACL"), { key: "version", label: "Version" }, { key: "vrf", label: "VRF", hidden: true }, { key: "group", label: "Group", hidden: true }] },
  { id: "aaa", title: "AAA", icon: "key", group: "Management", blurb: "Authentication servers, groups and method lists.",
    columns: [device, { key: "kind", label: "Kind" }, { key: "name", label: "Name" }, { key: "protocol", label: "Protocol" }, mono("address", "Address"), list("methods", "Methods"), list("members", "Members", { hidden: true })] },
  { id: "config", title: "Configuration sections", icon: "config", group: "Management", blurb: "Running configuration split into sections, secrets masked.",
    columns: [device, mono("key", "Section", { text: (r) => r.key, width: 280 }),
      { key: "lines", label: "Lines", sortable: false, render: (r) => <pre className="cfg">{(r.lines || []).join("\n") || "—"}</pre>, text: (r) => (r.lines || []).join(" ") }] },
];

export const TABLE_BY_ID: Record<string, TableDef> = Object.fromEntries(TABLE_DEFS.map((t) => [t.id, t]));
export const TECH_GROUPS = ["Interfaces", "Routing", "Layer 2", "Management"];
