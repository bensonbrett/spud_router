import { useState } from "react";
import { Btn, Card, ErrMsg, Field, Input, Pill, Row, Select, Toggle } from "../components/index.js";
import styles from "./VlansTab.module.css";
import sharedStyles from "./shared.module.css";
import { POST, DELETE } from "../api.js";

export function VlansTab({ state, interfaces, onReload, showToast }) {
  const [f, setF] = useState(defVlan);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));
  const vlans = state?.vlans || [];

  const submit = async () => {
    if (!f.vlan_id || !f.name || !f.ip_address) {
      setErr("VLAN ID, name, and gateway IP required.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await POST("/api/vlans", { ...f, vlan_id: parseInt(f.vlan_id), prefix_len: parseInt(f.prefix_len) });
      onReload();
      setF(defVlan); showToast(`VLAN ${f.vlan_id} added`);
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
          {vlans.map((v) => (
            <div key={v.vlan_id} className={styles.topologyVlan}>
              <span className={styles.topologyDim}>└─ </span>
              <span className={styles.topologyVlanId}>VLAN {v.vlan_id}</span>
              <span className={styles.topologyMeta}> · {v.name} · {v.ip_address}/{v.prefix_len}</span>
              {v.isolate && <span className={styles.topologyLock}> 🔒</span>}
            </div>
          ))}
        </div>
      </Card>

      <Card title={`VLANs (${vlans.length})`}>
        {vlans.length === 0 && <p className={sharedStyles.emptyState}>No VLANs yet.</p>}
        {vlans.map((v) => (
          <Row
            key={v.vlan_id}
            left={
              <>
                <span className={styles.vlanIdBadge}>{v.vlan_id}</span>
                {v.name}
              </>
            }
            sub={`${v.interface}.${v.vlan_id} · ${v.ip_address}/${v.prefix_len}${v.dhcp_enabled ? ` · DHCP ${v.dhcp_start}–${v.dhcp_end}` : ""}`}
            badges={[
              v.isolate    && <Pill key="iso"  variant="warning">isolated</Pill>,
              v.dhcp_enabled && <Pill key="dhcp" variant="success">dhcp</Pill>,
            ].filter(Boolean)}
            right={<Btn variant="danger" small onClick={async () => { await DELETE(`/api/vlans/${v.vlan_id}`); onReload(); showToast(`VLAN ${v.vlan_id} removed`); }}>✕</Btn>}
          />
        ))}
      </Card>

      <Card title="Add VLAN">
        <div className={sharedStyles.formGrid3}>
          <Field label="VLAN ID">
            <Input value={f.vlan_id} onChange={set("vlan_id")} placeholder="10" type="number" min="1" max="4094" />
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
        </div>
        <div className={sharedStyles.toggleRow}>
          <Toggle value={f.dhcp_enabled} onChange={set("dhcp_enabled")} label="Enable DHCP" />
          <Toggle value={f.isolate}      onChange={set("isolate")}      label="Isolate (block inter-VLAN)" />
        </div>
        <ErrMsg msg={err} />
        <Btn onClick={submit} disabled={busy}>{busy ? "Adding…" : "Add VLAN"}</Btn>
      </Card>
    </>
  );
}

// ── WAN tab ───────────────────────────────────────────────────────────────────