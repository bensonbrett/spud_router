import { useState } from "react";
import { Btn, Card, ErrMsg, Field, Input, Pill, Row, Select, Toggle } from "../components/index.js";
import styles from "./VlansTab.module.css";
import sharedStyles from "./shared.module.css";
import { POST, PUT, DELETE } from "../api.js";

const emptyForm = (defaultInterface) => ({
  vlan_id: "", name: "", interface: defaultInterface, ip_address: "",
  prefix_len: "24", dhcp_enabled: true, dhcp_start: "",
  dhcp_end: "", dhcp_lease: "12h", isolate: false,
  dns_server: "", dhcp_options: [],
});

export function VlansTab({ state, interfaces, onReload, showToast }) {
  const defaultInterface = state?.router?.mgmt_interface || interfaces?.[0]?.name || "eth0";
  const [f, setF] = useState(emptyForm(defaultInterface));
  const [editingId, setEditingId] = useState(null);
  const [dhcpOptInput, setDhcpOptInput] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));
  const vlans = state?.vlans || [];

  const startEdit = (v) => {
    setEditingId(v.vlan_id);
    setF({
      vlan_id: String(v.vlan_id), name: v.name, interface: v.interface,
      ip_address: v.ip_address || "", prefix_len: String(v.prefix_len || 24),
      dhcp_enabled: v.dhcp_enabled, dhcp_start: v.dhcp_start || "",
      dhcp_end: v.dhcp_end || "", dhcp_lease: v.dhcp_lease || "12h",
      isolate: !!v.isolate, dns_server: v.dns_server || "",
      dhcp_options: v.dhcp_options || [],
    });
    setErr("");
  };

  const cancelEdit = () => {
    setEditingId(null);
    setF(emptyForm(defaultInterface));
    setErr("");
  };

  const addDhcpOption = () => {
    if (dhcpOptInput && !f.dhcp_options.includes(dhcpOptInput)) {
      set("dhcp_options")([...f.dhcp_options, dhcpOptInput]);
      setDhcpOptInput("");
    }
  };

  const submit = async () => {
    if (!f.vlan_id || !f.name || !f.ip_address) {
      setErr("VLAN ID, name, and gateway IP required.");
      return;
    }
    setBusy(true);
    setErr("");
    const payload = { ...f, vlan_id: parseInt(f.vlan_id), prefix_len: parseInt(f.prefix_len) };
    try {
      if (editingId != null) {
        await PUT(`/api/vlans/${editingId}`, payload);
        showToast(`VLAN ${f.vlan_id} updated`);
      } else {
        await POST("/api/vlans", payload);
        showToast(`VLAN ${f.vlan_id} added`);
      }
      onReload();
      setEditingId(null);
      setF(emptyForm(defaultInterface));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Card title="Topology">
        <div className={styles.topology}>
          <span className={styles.topologyDim}>Internet</span>
          {" ── "}
          <span className={styles.topologyWan}>[WAN: {state?.router?.wan_interface || "eth1"}]</span>
          {" ── "}
          <span className={styles.topologyDevice}>Le Potato</span>
          {" ── "}
          <span className={styles.topologyTrunk}>[trunk: {vlans[0]?.interface || "eth0"}]</span>
          {" ── Switch"}
          {vlans.map((v) => {
            const isWan = v.ip_address === "" || v.ip_address === undefined;
            return (
              <div key={v.vlan_id} className={styles.topologyVlan}>
                <span className={styles.topologyDim}>└─ </span>
                <span className={styles.topologyVlanId}>VLAN {v.vlan_id}</span>
                <span className={styles.topologyMeta}> · {v.name}{isWan ? " (WAN)" : ` · ${v.ip_address}/${v.prefix_len}`}</span>
                {v.isolate && <span className={styles.topologyLock}> 🔒</span>}
              </div>
            );
          })}
        </div>
      </Card>

      <Card title={`VLANs (${vlans.length})`}>
        {vlans.length === 0 && <p className={sharedStyles.emptyState}>No VLANs yet.</p>}
        {vlans.map((v) => {
          const isWan = v.ip_address === "" || v.ip_address === undefined;
          return (
            <Row
              key={v.vlan_id}
              left={
                <>
                  <span className={styles.vlanIdBadge}>{v.vlan_id}</span>
                  {v.name}
                </>
              }
              sub={isWan 
                ? `${v.interface}.${v.vlan_id} · WAN (DHCP from ISP)`
                : `${v.interface}.${v.vlan_id} · ${v.ip_address}/${v.prefix_len}${v.dhcp_enabled ? ` · DHCP ${v.dhcp_start}–${v.dhcp_end}` : ""}`}
              badges={[
                isWan && <Pill key="wan" variant="info">WAN</Pill>,
                v.isolate    && <Pill key="iso"  variant="warning">isolated</Pill>,
                !isWan && v.dhcp_enabled && <Pill key="dhcp" variant="success">dhcp</Pill>,
              ].filter(Boolean)}
              right={!isWan && (
                <div className={styles.rowActions}>
                  <Btn small onClick={() => startEdit(v)}>Edit</Btn>
                  <Btn variant="danger" small onClick={async () => { await DELETE(`/api/vlans/${v.vlan_id}`); onReload(); showToast(`VLAN ${v.vlan_id} removed`); }}>✕</Btn>
                </div>
              )}
            />
          );
        })}
      </Card>

      <Card title={editingId != null ? `Edit VLAN ${editingId}` : "Add VLAN"}>
        <div className={sharedStyles.formGrid3}>
          <Field label="VLAN ID">
            <Input value={f.vlan_id} onChange={set("vlan_id")} placeholder="10" type="number" min="1" max="4094" disabled={editingId != null} />
          </Field>
          <Field label="Name">
            <Input value={f.name} onChange={set("name")} placeholder="Trusted" />
          </Field>
          <Field label="Parent Interface">
            <Select value={f.interface} onChange={set("interface")} options={interfaces.map((i) => ({ value: i.name, label: i.name }))} />
          </Field>
          <Field label="Gateway IP">
            <Input value={f.ip_address} onChange={set("ip_address")} placeholder="192.168.10.1" />
          </Field>
          <Field label="Prefix">
            <Input value={f.prefix_len} onChange={set("prefix_len")} type="number" min="8" max="30" />
          </Field>
          <Field label="DHCP Lease">
            <Select value={f.dhcp_lease} onChange={set("dhcp_lease")} options={[
              { value: "1h", label: "1 hour" }, { value: "6h", label: "6 hours" },
              { value: "12h", label: "12 hours" }, { value: "24h", label: "24 hours" },
              { value: "infinite", label: "Infinite" },
            ]} />
          </Field>
          <Field label="DHCP Start">
            <Input value={f.dhcp_start} onChange={set("dhcp_start")} placeholder="192.168.10.100" disabled={!f.dhcp_enabled} />
          </Field>
          <Field label="DHCP End">
            <Input value={f.dhcp_end} onChange={set("dhcp_end")} placeholder="192.168.10.200" disabled={!f.dhcp_enabled} />
          </Field>
          <Field label="Custom DNS Server" help="Handed out via DHCP option 6. Leave blank to use this VLAN's gateway.">
            <Input value={f.dns_server} onChange={set("dns_server")} placeholder="192.168.10.1" disabled={!f.dhcp_enabled} />
          </Field>
        </div>
        <Field label="Custom DHCP Options" help="Advanced: raw dnsmasq dhcp-option values, e.g. 42,192.168.10.1 for NTP.">
          <div className={styles.rowActions}>
            <Input
              value={dhcpOptInput}
              onChange={setDhcpOptInput}
              placeholder="42,192.168.10.1"
              disabled={!f.dhcp_enabled}
              onKeyDown={(e) => e.key === "Enter" && addDhcpOption()}
            />
            <Btn small onClick={addDhcpOption} disabled={!f.dhcp_enabled}>+ Add</Btn>
          </div>
          <div className={styles.tagList}>
            {f.dhcp_options.map((opt) => (
              <span key={opt} className={styles.tag}>
                {opt}
                <button className={styles.tagRemove} onClick={() => set("dhcp_options")(f.dhcp_options.filter((x) => x !== opt))}>×</button>
              </span>
            ))}
            {f.dhcp_options.length === 0 && <span className={sharedStyles.emptyState}>No custom DHCP options</span>}
          </div>
        </Field>
        <div className={sharedStyles.toggleRow}>
          <Toggle value={f.dhcp_enabled} onChange={set("dhcp_enabled")} label="Enable DHCP" />
          <Toggle value={f.isolate}      onChange={set("isolate")}      label="Isolate (block inter-VLAN)" />
        </div>
        <ErrMsg msg={err} />
        <div className={styles.rowActions}>
          <Btn onClick={submit} disabled={busy}>
            {busy ? (editingId != null ? "Saving…" : "Adding…") : (editingId != null ? "Save Changes" : "Add VLAN")}
          </Btn>
          {editingId != null && <Btn variant="ghost" onClick={cancelEdit} disabled={busy}>Cancel</Btn>}
        </div>
      </Card>
    </>
  );
}

// ── WAN tab ───────────────────────────────────────────────────────────────────