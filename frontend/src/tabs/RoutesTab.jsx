import { useState } from "react";
import { Btn, Card, ErrMsg, Field, Input, Row, Select } from "../components/index.js";
import styles from "./shared.module.css";
import { POST, DELETE } from "../api.js";

const defRoute = { destination: "", gateway: "", interface: "", description: "" };


export function RoutesTab({ state, onReload, showToast }) {
  const [f, setF] = useState(defRoute);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));
  const routes = state?.static_routes || [];
  const vlans  = state?.vlans || [];
  const ifOpts = [
    { value: "", label: "(auto)" },
    ...vlans.map((v) => ({ value: `${v.interface}.${v.vlan_id}`, label: `${v.interface}.${v.vlan_id} (${v.name})` })),
  ];

  const submit = async () => {
    if (!f.destination || !f.gateway) { setErr("Destination and gateway required."); return; }
    setBusy(true);
    setErr("");
    try { await POST("/api/routes", f); setF(defRoute); showToast("Route added"); }
    catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <Card title={`Static Routes (${routes.length})`}>
        {routes.length === 0 && <p className={styles.emptyState}>No static routes.</p>}
        {routes.map((r) => (
          <Row
            key={r.destination}
            left={<span className={styles.mono}>{r.destination}</span>}
            sub={`via ${r.gateway}${r.interface ? ` dev ${r.interface}` : ""}${r.description ? ` · ${r.description}` : ""}`}
            right={<Btn variant="danger" small onClick={async () => { await DELETE(`/api/routes/${encodeURIComponent(r.destination)}`); onReload(); showToast("Route removed"); }}>✕</Btn>}
          />
        ))}
      </Card>

      <Card title="Add Static Route">
        <div className={styles.formGrid2}>
          <Field label="Destination CIDR">
            <Input value={f.destination} onChange={set("destination")} placeholder="10.0.0.0/8" />
          </Field>
          <Field label="Via Gateway">
            <Input value={f.gateway} onChange={set("gateway")} placeholder="192.168.10.254" />
          </Field>
          <Field label="Interface">
            <Select value={f.interface} onChange={set("interface")} options={ifOpts} />
          </Field>
          <Field label="Description">
            <Input value={f.description} onChange={set("description")} placeholder="Corp VPN" />
          </Field>
        </div>
        <ErrMsg msg={err} />
        <div className={styles.formActions}>
          <Btn onClick={submit} disabled={busy}>{busy ? "Adding…" : "Add Route"}</Btn>
        </div>
      </Card>
    </>
  );
}

// ── Firewall tab ──────────────────────────────────────────────────────────────
const PROTO_OPTS  = [{ value: "any", label: "Any" }, { value: "tcp", label: "TCP" }, { value: "udp", label: "UDP" }];
const ACTION_OPTS = [{ value: "accept", label: "Allow" }, { value: "drop", label: "Drop" }];
const COMMON_PORTS = [
  { label: "SSH (22)",      port: 22,   proto: "tcp" },
  { label: "HTTP (80)",     port: 80,   proto: "tcp" },
  { label: "HTTPS (443)",   port: 443,  proto: "tcp" },
  { label: "Web UI (8080)", port: 8080, proto: "tcp" },
  { label: "DNS (53)",      port: 53,   proto: "udp" },
];

const defInbound   = { vlan_id: "0", proto: "tcp", port: "", action: "accept", description: "" };
const defIntervlan = { from_vlan: "0", to_vlan: "0", proto: "any", port: "", action: "accept", description: "" };
