// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState, useEffect } from "react";
import { GET, POST } from "../api.js";
import { Btn, Card, CodeBlock, ErrMsg, Field, Input, OkMsg, Select } from "../components/index.js";
import styles from "./DiagnosticsTab.module.css";
import sharedStyles from "./shared.module.css";

const COMMAND_OPTS = [
  { value: "ping", label: "Ping" },
  { value: "traceroute", label: "Traceroute" },
  { value: "nslookup", label: "Nslookup" },
];

// Client-side mirror of models.py's WolRequest MAC regex — this is only a
// UX nicety (fast feedback before a round trip), never the security
// boundary; the backend re-validates and normalizes independently.
const MAC_RE = /^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$/;

function CommandPanel() {
  const [command, setCommand] = useState("ping");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);

  const run = async () => {
    if (!target.trim()) { setErr("Enter a target host or IP."); return; }
    setBusy(true); setErr(""); setResult(null);
    try {
      const res = await POST("/api/diagnostics/run", { command, target: target.trim() });
      setResult(res);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Run a diagnostic command">
      <div className={sharedStyles.formGrid3}>
        <Field label="Command"><Select value={command} onChange={setCommand} options={COMMAND_OPTS} /></Field>
        <Field label="Target" help="Hostname or IP address">
          <Input
            value={target}
            onChange={setTarget}
            placeholder="8.8.8.8 or example.com"
            onKeyDown={(e) => e.key === "Enter" && !busy && run()}
          />
        </Field>
      </div>
      <ErrMsg msg={err} />
      <Btn onClick={run} disabled={busy}>{busy ? "Running…" : "Run"}</Btn>
      {result && (
        <div className={styles.diagResult}>
          {result.timed_out && <p className={sharedStyles.emptyState}>⚠ Command timed out — showing partial output.</p>}
          {result.truncated && <p className={sharedStyles.emptyState}>⚠ Output truncated.</p>}
          <CodeBlock content={result.output || "(no output)"} />
        </div>
      )}
    </Card>
  );
}

function WolPanel() {
  const [vlans, setVlans] = useState([]);
  const [mac, setMac] = useState("");
  const [vlanId, setVlanId] = useState("");
  const [broadcast, setBroadcast] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);

  // This component isn't passed `state` as a prop today, so fetch VLANs
  // directly — same pattern CommandPanel/DiagnosticsTab already use for
  // /api/diagnostics rather than threading state down from App.jsx.
  useEffect(() => { GET("/api/vlans").then(setVlans).catch(() => {}); }, []);

  const vlanOpts = [
    { value: "", label: "Any (255.255.255.255)" },
    ...vlans
      .filter((v) => v.ip_address)
      .map((v) => ({ value: String(v.vlan_id), label: `VLAN ${v.vlan_id} · ${v.name}` })),
  ];

  const send = async () => {
    const trimmed = mac.trim();
    if (!MAC_RE.test(trimmed)) {
      setErr("Enter a valid MAC address, e.g. aa:bb:cc:dd:ee:ff");
      return;
    }
    setBusy(true); setErr(""); setResult(null);
    try {
      const body = { mac: trimmed };
      if (vlanId) body.vlan_id = Number(vlanId);
      else if (broadcast.trim()) body.broadcast = broadcast.trim();
      const res = await POST("/api/diagnostics/wol", body);
      setResult(res);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Wake-on-LAN">
      <div className={sharedStyles.formGrid3}>
        <Field label="MAC address" help="e.g. aa:bb:cc:dd:ee:ff">
          <Input
            value={mac}
            onChange={setMac}
            placeholder="aa:bb:cc:dd:ee:ff"
            onKeyDown={(e) => e.key === "Enter" && !busy && send()}
          />
        </Field>
        <Field label="VLAN" help="Broadcast domain for the magic packet">
          <Select value={vlanId} onChange={setVlanId} options={vlanOpts} />
        </Field>
        {!vlanId && (
          <Field label="Custom broadcast address" help="Optional — overrides 255.255.255.255, e.g. for a routed subnet">
            <Input value={broadcast} onChange={setBroadcast} placeholder="255.255.255.255" />
          </Field>
        )}
      </div>
      <ErrMsg msg={err} />
      <Btn onClick={send} disabled={busy}>{busy ? "Sending…" : "Send WOL"}</Btn>
      {result && (
        result.sent
          ? <OkMsg msg={`Magic packet sent to ${result.mac} via ${result.broadcast}`} />
          : <ErrMsg msg={`Failed to send: ${result.error || "unknown error"}`} />
      )}
    </Card>
  );
}

function StatusDot({ carrier, operstate }) {
  const up = carrier === true && operstate === "up";
  const carrier_only = carrier === true && operstate !== "up";
  const cls = up ? styles.dotUp : carrier_only ? styles.dotPartial : styles.dotDown;
  const label = carrier === null ? "no interface" : up ? "up" : carrier_only ? `carrier, ${operstate}` : "down";
  return <span className={`${styles.dot} ${cls}`} title={label} />;
}

function IfaceCard({ iface }) {
  const isUp = iface.carrier === true && iface.operstate === "up";
  return (
    <Card title={
      <span className={styles.cardTitle}>
        <StatusDot carrier={iface.carrier} operstate={iface.operstate} />
        <span className={styles.ifaceName}>{iface.name}</span>
        {iface.vlan_name && <span className={styles.vlanBadge}>VLAN {iface.vlan_id} · {iface.vlan_name}</span>}
        {iface.role === "wan" && <span className={styles.roleBadge}>WAN</span>}
        {iface.is_default_gw && <span className={styles.gwBadge}>default GW</span>}
      </span>
    }>
      <div className={styles.ifaceBody}>
        <div className={styles.row}>
          <span className={styles.label}>State</span>
          <span className={isUp ? styles.valOk : styles.valWarn}>
            {iface.carrier === null ? "interface not found" : `${iface.operstate} (carrier: ${iface.carrier ? "yes" : "no"})`}
          </span>
        </div>
        {iface.cfg_address && (
          <div className={styles.row}>
            <span className={styles.label}>Configured IP</span>
            <span className={iface.ip_present ? styles.valOk : styles.valWarn}>
              {iface.cfg_address}
              {iface.ip_present ? " ✓" : " — not assigned"}
            </span>
          </div>
        )}
        {iface.addresses?.length > 0 && (
          <div className={styles.row}>
            <span className={styles.label}>Addresses</span>
            <span className={styles.mono}>{iface.addresses.join(", ")}</span>
          </div>
        )}
        {iface.role === "vlan" && (
          <div className={styles.row}>
            <span className={styles.label}>DHCP leases</span>
            <span className={styles.mono}>
              {iface.leases?.length > 0
                ? iface.leases.map(l => `${l.ip} (${l.hostname})`).join(", ")
                : "none"}
            </span>
          </div>
        )}
        {iface.hint && (
          <div className={styles.hint}>⚠ {iface.hint}</div>
        )}
      </div>
    </Card>
  );
}

export function DiagnosticsTab() {
  const [data, setData] = useState(null);
  const load = () => GET("/api/diagnostics").then(setData).catch(() => {});
  useEffect(() => { load(); }, []);

  return (
    <>
      <CommandPanel />
      <WolPanel />

      <div className={sharedStyles.refreshRow}>
        <Btn variant="ghost" small onClick={load}>↻ Refresh</Btn>
      </div>
      {data && (
        <>
          {data.default_route && (
            <div className={styles.routeBanner}>
              <span className={styles.routeLabel}>Default route</span>
              <span className={styles.routeVal}>{data.default_route}</span>
            </div>
          )}
          <div className={styles.grid}>
            {data.wan && <IfaceCard iface={data.wan} />}
            {data.vlans?.map(v => <IfaceCard key={v.name} iface={v} />)}
          </div>
          {!data.wan && data.vlans?.length === 0 && (
            <p className={sharedStyles.emptyState}>No interfaces configured. Add a WAN or VLAN first.</p>
          )}
        </>
      )}
    </>
  );
}
