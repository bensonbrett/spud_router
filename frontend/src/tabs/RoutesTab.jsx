// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState, useEffect } from "react";
import { Btn, Card, ErrMsg, Field, Input, Pill, Row, Select, Toggle } from "../components/index.js";
import styles from "./shared.module.css";
import { GET, POST, PUT, DELETE } from "../api.js";

const defRoute = { destination: "", gateway: "", interface: "", description: "" };
const defBgp = { enabled: false, asn: null, router_id: null, neighbors: [], networks: [] };
const defNeighbor = { ip: "", remote_as: "", description: "" };

// Polls GET /api/bgp/status while BGP is enabled — same shape as
// StatusTab's system-monitor poll (interval cleared on unmount so it never
// leaks across tab switches).
const BGP_STATUS_POLL_MS = 3000;

const BGP_STATE_VARIANT = {
  Established: "success",
  Active: "warning",
  Connect: "warning",
  Idle: "muted",
};

function BgpSection({ state, onReload, showToast }) {
  const [f, setF] = useState(state?.bgp || defBgp);
  const [asnInput, setAsnInput] = useState("");
  const [routerIdInput, setRouterIdInput] = useState("");
  const [neighborForm, setNeighborForm] = useState(defNeighbor);
  const [networkInput, setNetworkInput] = useState("");
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const bgp = state?.bgp || defBgp;
    setF(bgp);
    setAsnInput(bgp.asn != null ? String(bgp.asn) : "");
    setRouterIdInput(bgp.router_id || "");
  }, [state]);

  useEffect(() => {
    const poll = () => GET("/api/bgp/status").then(setStatus).catch(() => {});
    poll();
    const id = setInterval(poll, BGP_STATUS_POLL_MS);
    return () => clearInterval(id);
  }, []);

  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));

  // Persists an explicitly-computed body rather than reading `f` from
  // closure — setF() is async, so a persist() called right after setF()
  // in the same handler would otherwise save the PRE-update value.
  const persist = async (body) => {
    setBusy(true);
    setErr("");
    try {
      await PUT("/api/bgp", body);
      setF(body);
      onReload();
      showToast("BGP config saved");
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const save = () => {
    const asn = asnInput ? parseInt(asnInput, 10) : null;
    if (asnInput && Number.isNaN(asn)) { setErr("ASN must be a number."); return; }
    persist({ ...f, asn, router_id: routerIdInput || null });
  };

  const addNeighbor = () => {
    if (!neighborForm.ip || !neighborForm.remote_as) return;
    const remoteAs = parseInt(neighborForm.remote_as, 10);
    if (Number.isNaN(remoteAs)) { setErr("Remote AS must be a number."); return; }
    const neighbors = [...f.neighbors, { ...neighborForm, remote_as: remoteAs }];
    setNeighborForm(defNeighbor);
    persist({ ...f, neighbors });
  };

  const removeNeighbor = (ip) => {
    persist({ ...f, neighbors: f.neighbors.filter((n) => n.ip !== ip) });
  };

  const addNetwork = () => {
    if (!networkInput || f.networks.includes(networkInput)) return;
    const networks = [...f.networks, networkInput];
    setNetworkInput("");
    persist({ ...f, networks });
  };

  const removeNetwork = (cidr) => {
    persist({ ...f, networks: f.networks.filter((n) => n !== cidr) });
  };

  const statusByIp = Object.fromEntries((status?.neighbors || []).map((n) => [n.ip, n]));

  return (
    <>
      <Card title="BGP">
        <Toggle value={f.enabled} onChange={set("enabled")} label="Enable BGP (FRR)" />
        <div className={styles.formGrid2}>
          <Field label="Local ASN">
            <Input value={asnInput} onChange={setAsnInput} placeholder="65001" />
          </Field>
          <Field label="Router ID">
            <Input value={routerIdInput} onChange={setRouterIdInput} placeholder="10.0.0.1" />
          </Field>
        </div>
        <ErrMsg msg={err} />
        <div className={styles.formActions}>
          <Btn onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</Btn>
        </div>
      </Card>

      <Card title={`BGP Neighbors (${f.neighbors.length})`}>
        {f.neighbors.length === 0 && <p className={styles.emptyState}>No neighbors configured.</p>}
        {f.neighbors.map((n) => {
          const live = statusByIp[n.ip];
          return (
            <Row
              key={n.ip}
              left={<span className={styles.mono}>{n.ip}</span>}
              sub={`AS ${n.remote_as}${n.description ? ` · ${n.description}` : ""}${live?.pfx_rcvd != null ? ` · ${live.pfx_rcvd} rcvd` : ""}${live?.pfx_sent != null ? ` / ${live.pfx_sent} sent` : ""}`}
              badges={f.enabled && live ? [
                <Pill key="s" variant={BGP_STATE_VARIANT[live.state] || "muted"}>{live.state}</Pill>,
              ] : []}
              right={<Btn variant="danger" small onClick={() => removeNeighbor(n.ip)}>✕</Btn>}
            />
          );
        })}
        <div className={styles.formGrid2}>
          <Field label="Neighbor IP">
            <Input value={neighborForm.ip} onChange={(v) => setNeighborForm((p) => ({ ...p, ip: v }))} placeholder="192.168.10.2" />
          </Field>
          <Field label="Remote AS">
            <Input value={neighborForm.remote_as} onChange={(v) => setNeighborForm((p) => ({ ...p, remote_as: v }))} placeholder="65002" />
          </Field>
          <Field label="Description">
            <Input value={neighborForm.description} onChange={(v) => setNeighborForm((p) => ({ ...p, description: v }))} placeholder="Upstream ISP" />
          </Field>
        </div>
        <div className={styles.formActions}>
          <Btn onClick={addNeighbor}>+ Add Neighbor</Btn>
        </div>
      </Card>

      <Card title={`Advertised Networks (${f.networks.length})`}>
        {f.networks.length === 0 && <p className={styles.emptyState}>No advertised networks.</p>}
        {f.networks.map((n) => (
          <Row
            key={n}
            left={<span className={styles.mono}>{n}</span>}
            right={<Btn variant="danger" small onClick={() => removeNetwork(n)}>✕</Btn>}
          />
        ))}
        <div className={styles.formGrid2}>
          <Field label="Network CIDR">
            <Input value={networkInput} onChange={setNetworkInput} placeholder="10.0.0.0/24" onKeyDown={(e) => e.key === "Enter" && addNetwork()} />
          </Field>
        </div>
        <div className={styles.formActions}>
          <Btn onClick={addNetwork}>+ Add Network</Btn>
        </div>
      </Card>
    </>
  );
}

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
            right={<Btn variant="danger" small onClick={async () => {
              try {
                await DELETE(`/api/routes/${encodeURIComponent(r.destination)}`);
                onReload();
                showToast("Route removed");
              } catch (e) {
                if (!e.isAuthError) showToast("Remove failed: " + e.message);
              }
            }}>✕</Btn>}
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

      <BgpSection state={state} onReload={onReload} showToast={showToast} />
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
