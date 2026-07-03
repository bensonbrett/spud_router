import { useState } from "react";
import { Btn, Card, ErrMsg, Field, Input, Pill, Row, Select } from "../components/index.js";
import styles from "./FirewallTab.module.css";
import sharedStyles from "./shared.module.css";
import { POST, PUT, DELETE } from "../api.js";

const defInbound   = { vlan_id: "0", proto: "tcp", port: "", action: "accept", description: "", icmp_type: "", icmp_code: "" };
const defIntervlan = { from_vlan: "0", to_vlan: "0", proto: "any", port: "", action: "accept", description: "", icmp_type: "", icmp_code: "" };
const defOutbound  = { vlan_id: "0", dest: "", proto: "any", port: "", action: "accept", description: "", icmp_type: "", icmp_code: "" };

const COMMON_PORTS = [
  { label: "SSH", port: 22, proto: "tcp" },
  { label: "HTTP", port: 80, proto: "tcp" },
  { label: "HTTPS", port: 443, proto: "tcp" },
  { label: "DNS", port: 53, proto: "udp" },
  { label: "DHCP", port: 67, proto: "udp" },
  { label: "RDP", port: 3389, proto: "tcp" },
  { label: "SMB", port: 445, proto: "tcp" },
];

const PROTO_OPTS = [
  { value: "tcp", label: "TCP" },
  { value: "udp", label: "UDP" },
  { value: "icmp", label: "ICMP" },
  { value: "any", label: "Any" },
];

const ICMP_TYPE_OPTS = [
  { value: "", label: "Any" },
  { value: "echo-request", label: "Echo request (ping)" },
  { value: "echo-reply", label: "Echo reply" },
  { value: "destination-unreachable", label: "Destination unreachable" },
  { value: "time-exceeded", label: "Time exceeded" },
  { value: "custom", label: "Custom (numeric)…" },
];

const ACTION_OPTS = [
  { value: "accept", label: "Accept" },
  { value: "drop", label: "Drop" },
];

const ICMP_NAMED_TYPES = new Set(ICMP_TYPE_OPTS.map((o) => o.value).filter((v) => v && v !== "custom"));

// ICMP type/code inputs shown in place of the Port field when proto === "icmp".
function IcmpTypeCodeFields({ type, code, onType, onCode }) {
  const isCustom = type !== "" && !ICMP_NAMED_TYPES.has(type);
  return (
    <>
      <Field label="ICMP Type" help="Leave as Any to match every type">
        <Select
          value={isCustom ? "custom" : type}
          onChange={(v) => onType(v === "custom" ? "0" : v)}
          options={ICMP_TYPE_OPTS}
        />
      </Field>
      {isCustom && (
        <Field label="Type (numeric)" help="0–255">
          <Input value={type} onChange={onType} type="number" min="0" max="255" placeholder="8" />
        </Field>
      )}
      <Field label="ICMP Code" help="Optional, 0–255">
        <Input value={code} onChange={onCode} type="number" min="0" max="255" placeholder="" />
      </Field>
    </>
  );
}


export function FirewallTab({ state, onReload, showToast }) {
  const [section, setSection] = useState("inbound");
  const [fi,  setFi]  = useState(defInbound);
  const [fiv, setFiv] = useState(defIntervlan);
  const [fo,  setFo]  = useState(defOutbound);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmingDeny, setConfirmingDeny] = useState(false);
  const seti  = (k) => (v) => setFi((p)  => ({ ...p, [k]: v }));
  const setiv = (k) => (v) => setFiv((p) => ({ ...p, [k]: v }));
  const seto  = (k) => (v) => setFo((p)  => ({ ...p, [k]: v }));

  const vlans    = state?.vlans || [];
  const fw_in    = state?.fw_inbound   || [];
  const fw_iv    = state?.fw_intervlan || [];
  const fw_out   = state?.fw_outbound  || [];
  const outboundDefault = state?.fw_outbound_default || "allow";
  const vlanOpts = [{ value: "0", label: "All VLANs" }, ...vlans.map((v) => ({ value: String(v.vlan_id), label: `VLAN ${v.vlan_id} — ${v.name}` }))];
  const vlanMap  = Object.fromEntries(vlans.map((v) => [v.vlan_id, v.name]));
  const vlanName = (id) => (id === 0 ? "All VLANs" : vlanMap[id] || `VLAN ${id}`);
  const protoPort = (r) => {
    if (!r.proto || r.proto === "any") return "any";
    if (r.proto === "icmp") {
      const t = r.icmp_type ? `/${r.icmp_type}${r.icmp_code != null && r.icmp_code !== "" ? `:${r.icmp_code}` : ""}` : "";
      return `ICMP${t}`;
    }
    return `${r.proto.toUpperCase()}${r.port ? `:${r.port}` : ""}`;
  };
  const hasIvRules = fw_iv.length > 0;

  const icmpFields = (f) => f.proto === "icmp"
    ? { icmp_type: f.icmp_type || null, icmp_code: f.icmp_code !== "" && f.icmp_code != null ? parseInt(f.icmp_code) : null }
    : { icmp_type: null, icmp_code: null };

  const submitInbound = async () => {
    setBusy(true); setErr("");
    try {
      await POST("/api/firewall/inbound", { ...fi, vlan_id: parseInt(fi.vlan_id), port: fi.port ? parseInt(fi.port) : null, ...icmpFields(fi) });
      onReload();
      showToast("Inbound rule added");
      setFi(defInbound);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const submitIntervlan = async () => {
    setBusy(true); setErr("");
    try {
      await POST("/api/firewall/intervlan", { ...fiv, from_vlan: parseInt(fiv.from_vlan), to_vlan: parseInt(fiv.to_vlan), port: fiv.port ? parseInt(fiv.port) : null, ...icmpFields(fiv) });
      onReload();
      showToast("Inter-VLAN rule added");
      setFiv(defIntervlan);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const submitOutbound = async () => {
    setBusy(true); setErr("");
    try {
      await POST("/api/firewall/outbound", { ...fo, vlan_id: parseInt(fo.vlan_id), port: fo.port ? parseInt(fo.port) : null, ...icmpFields(fo) });
      onReload();
      showToast("Outbound rule added");
      setFo(defOutbound);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const applyOutboundDefault = async (value) => {
    setBusy(true); setErr("");
    try {
      await PUT("/api/firewall/outbound/default", { default: value });
      onReload();
      showToast(`Default outbound policy: ${value}`);
      setConfirmingDeny(false);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const handleOutboundDefaultClick = (value) => {
    if (value === "deny" && outboundDefault !== "deny") {
      setConfirmingDeny(true);
    } else {
      applyOutboundDefault(value);
    }
  };

  return (
    <>
      {hasIvRules && (
        <div className={styles.alertWarning}>
          ⚠ <strong>Explicit mode active</strong> — inter-VLAN traffic is default-deny. Only the rules below are permitted.
        </div>
      )}
      {!hasIvRules && (
        <div className={styles.alertInfo}>
          ℹ <strong>Auto mode</strong> — non-isolated VLANs are fully meshed. Add an inter-VLAN rule to switch to explicit mode.
        </div>
      )}

      <div className={styles.sectionTabs}>
        {[["inbound", "🛡 Inbound Rules", fw_in.length], ["intervlan", "↔ Inter-VLAN Rules", fw_iv.length], ["outbound", "→ Outbound Rules", fw_out.length]].map(([id, label, count]) => (
          <button
            key={id}
            className={styles.sectionTab}
            data-active={section === id}
            onClick={() => setSection(id)}
          >
            {label}
            <span className={styles.sectionTabCount}>{count}</span>
          </button>
        ))}
      </div>

      {section === "inbound" && (
        <>
          <Card title="Inbound Rules — traffic reaching the router itself">
            <p className={styles.settingsBackupDesc}>
              DNS (53) and DHCP (67) are always open on all VLANs. These rules control additional access.
            </p>
            {fw_in.length === 0 && (
              <p className={sharedStyles.emptyState}>
                No custom inbound rules — SSH and web UI are blocked from all VLANs by default.
              </p>
            )}
            {fw_in.map((r) => (
              <Row
                key={r.id}
                left={
                  <>
                    <span className={r.action === "accept" ? styles.actionAllow : styles.actionDrop}>
                      {r.action === "accept" ? "ALLOW" : "DROP"}
                    </span>
                    {r.description || protoPort(r)}
                  </>
                }
                sub={`VLAN: ${vlanName(r.vlan_id)} · ${protoPort(r)}`}
                badges={[<Pill key="a" variant={r.action === "accept" ? "success" : "danger"}>{r.action}</Pill>]}
                right={<Btn variant="danger" small onClick={async () => { await DELETE(`/api/firewall/inbound/${r.id}`); onReload(); showToast("Rule removed"); }}>✕</Btn>}
              />
            ))}
          </Card>

          <Card title="Add Inbound Rule">
            <div className={styles.presetRow}>
              {COMMON_PORTS.map((p) => (
                <button key={p.label} className={styles.presetBtn} onClick={() => { seti("port")(String(p.port)); seti("proto")(p.proto); }}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className={sharedStyles.formGrid3}>
              <Field label="VLAN"><Select value={fi.vlan_id} onChange={seti("vlan_id")} options={vlanOpts} /></Field>
              <Field label="Protocol"><Select value={fi.proto} onChange={seti("proto")} options={PROTO_OPTS} /></Field>
              {fi.proto === "icmp" ? (
                <IcmpTypeCodeFields type={fi.icmp_type} code={fi.icmp_code} onType={seti("icmp_type")} onCode={seti("icmp_code")} />
              ) : (
                <Field label="Port" help="Leave blank for all"><Input value={fi.port} onChange={seti("port")} placeholder="22" type="number" /></Field>
              )}
              <Field label="Action"><Select value={fi.action} onChange={seti("action")} options={ACTION_OPTS} /></Field>
              <Field label="Description"><Input value={fi.description} onChange={seti("description")} placeholder="SSH from Trusted" /></Field>
            </div>
            <ErrMsg msg={err} />
            <Btn onClick={submitInbound} disabled={busy}>{busy ? "Adding…" : "Add Rule"}</Btn>
          </Card>
        </>
      )}

      {section === "intervlan" && (
        <>
          {vlans.length >= 2 && (
            <Card title="Access Matrix">
              <div className={styles.scrollX}>
                <table className={styles.matrix}>
                  <thead>
                    <tr>
                      <th className={`${styles.matrixTh} ${styles.matrixThLeft}`}>From ↓  To →</th>
                      {vlans.map((v) => (
                        <th key={v.vlan_id} className={styles.matrixTh}>
                          <div className={styles.matrixFromLabel}>{v.vlan_id}</div>
                          <div className={styles.matrixSubLabel}>{v.name}</div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {vlans.map((from) => (
                      <tr key={from.vlan_id}>
                        <td className={`${styles.matrixTd} ${styles.matrixTdLabel}`}>
                          <span className={styles.matrixFromLabel}>{from.vlan_id}</span> {from.name}
                        </td>
                        {vlans.map((to) => {
                          const isSelf = from.vlan_id === to.vlan_id;
                          let state, label;
                          if (isSelf) { state = "self"; label = "—"; }
                          else if (hasIvRules) {
                            const match = fw_iv.find((r) => (r.from_vlan === from.vlan_id || r.from_vlan === 0) && (r.to_vlan === to.vlan_id || r.to_vlan === 0));
                            state = match?.action === "accept" ? "allow" : "deny";
                            label = state === "allow" ? "✓" : "✕";
                          } else {
                            state = !from.isolate && !to.isolate ? "allow" : "lock";
                            label = state === "allow" ? "✓" : "🔒";
                          }
                          return (
                            <td key={to.vlan_id} className={styles.matrixTd}>
                              <span className={styles.matrixCell} data-state={state}>{label}</span>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className={styles.matrixNote}>
                {hasIvRules ? "Explicit mode — matrix shows first matching rule." : "Auto mode — 🔒 = isolated VLAN."}
              </p>
            </Card>
          )}

          <Card title="Inter-VLAN Rules">
            {fw_iv.length === 0 && <p className={sharedStyles.emptyState}>No rules. Running in auto mode.</p>}
            {fw_iv.map((r) => (
              <Row
                key={r.id}
                left={
                  <>
                    <span className={r.action === "accept" ? styles.actionAllow : styles.actionDrop}>
                      {r.action === "accept" ? "ALLOW" : "DROP"}
                    </span>
                    {r.description || `VLAN ${r.from_vlan} → VLAN ${r.to_vlan}`}
                  </>
                }
                sub={`${vlanName(r.from_vlan)} → ${vlanName(r.to_vlan)} · ${protoPort(r)}`}
                badges={[<Pill key="a" variant={r.action === "accept" ? "success" : "danger"}>{r.action}</Pill>]}
                right={<Btn variant="danger" small onClick={async () => { await DELETE(`/api/firewall/intervlan/${r.id}`); onReload(); showToast("Rule removed"); }}>✕</Btn>}
              />
            ))}
          </Card>

          <Card title="Add Inter-VLAN Rule">
            <div className={sharedStyles.formGrid3}>
              <Field label="From VLAN"><Select value={fiv.from_vlan} onChange={setiv("from_vlan")} options={vlanOpts} /></Field>
              <Field label="To VLAN"><Select value={fiv.to_vlan} onChange={setiv("to_vlan")} options={vlanOpts} /></Field>
              <Field label="Action"><Select value={fiv.action} onChange={setiv("action")} options={ACTION_OPTS} /></Field>
              <Field label="Protocol"><Select value={fiv.proto} onChange={setiv("proto")} options={PROTO_OPTS} /></Field>
              {fiv.proto === "icmp" ? (
                <IcmpTypeCodeFields type={fiv.icmp_type} code={fiv.icmp_code} onType={setiv("icmp_type")} onCode={setiv("icmp_code")} />
              ) : (
                <Field label="Port" help="Leave blank for all"><Input value={fiv.port} onChange={setiv("port")} placeholder="443" type="number" /></Field>
              )}
              <Field label="Description"><Input value={fiv.description} onChange={setiv("description")} placeholder="Trusted → IoT HTTPS" /></Field>
            </div>
            <ErrMsg msg={err} />
            <Btn onClick={submitIntervlan} disabled={busy}>{busy ? "Adding…" : "Add Rule"}</Btn>
          </Card>
        </>
      )}

      {section === "outbound" && (
        <>
          <Card title="Default Egress Policy">
            <p className={styles.settingsBackupDesc}>
              Default: LAN VLANs {outboundDefault === "allow" ? "may reach the internet (WAN)" : "may NOT reach the internet (WAN)"}.
              Rules below are evaluated top-to-bottom — first match wins; unmatched traffic falls through to this default.
            </p>
            <div className={styles.confirmRow}>
              <Btn variant={outboundDefault === "allow" ? "primary" : "ghost"} small onClick={() => handleOutboundDefaultClick("allow")} disabled={busy}>
                Allow
              </Btn>
              <Btn variant={outboundDefault === "deny" ? "danger" : "ghost"} small onClick={() => handleOutboundDefaultClick("deny")} disabled={busy}>
                Deny
              </Btn>
            </div>
            {confirmingDeny && (
              <div className={styles.alertWarning}>
                ⚠ This blocks all LAN internet access except what your rules explicitly allow. The
                router's own connectivity and the web UI are unaffected, but LAN devices will lose
                internet until you add allow rules.
                <div className={styles.confirmRow}>
                  <Btn variant="danger" small onClick={() => applyOutboundDefault("deny")} disabled={busy}>
                    Yes, switch to Deny
                  </Btn>
                  <Btn variant="ghost" small onClick={() => setConfirmingDeny(false)} disabled={busy}>
                    Cancel
                  </Btn>
                </div>
              </div>
            )}
          </Card>

          <Card title="Outbound Rules — LAN VLANs → WAN">
            {fw_out.length === 0 && (
              <p className={sharedStyles.emptyState}>
                No custom outbound rules — every LAN VLAN falls through to the default above.
              </p>
            )}
            {fw_out.map((r) => (
              <Row
                key={r.id}
                left={
                  <>
                    <span className={r.action === "accept" ? styles.actionAllow : styles.actionDrop}>
                      {r.action === "accept" ? "ALLOW" : "DROP"}
                    </span>
                    {r.description || protoPort(r)}
                  </>
                }
                sub={`VLAN: ${vlanName(r.vlan_id)} → ${r.dest || "any"} · ${protoPort(r)}`}
                badges={[<Pill key="a" variant={r.action === "accept" ? "success" : "danger"}>{r.action}</Pill>]}
                right={<Btn variant="danger" small onClick={async () => { await DELETE(`/api/firewall/outbound/${r.id}`); onReload(); showToast("Rule removed"); }}>✕</Btn>}
              />
            ))}
            <p className={styles.matrixNote}>
              Rules are appended in the order added and evaluated top-to-bottom — the first match
              (by source VLAN) wins; reordering isn't supported yet.
            </p>
          </Card>

          <Card title="Add Outbound Rule">
            <div className={sharedStyles.formGrid3}>
              <Field label="Source VLAN"><Select value={fo.vlan_id} onChange={seto("vlan_id")} options={vlanOpts} /></Field>
              <Field label="Destination" help="Blank = any"><Input value={fo.dest} onChange={seto("dest")} placeholder="0.0.0.0/0 or 8.8.8.8" /></Field>
              <Field label="Protocol"><Select value={fo.proto} onChange={seto("proto")} options={PROTO_OPTS} /></Field>
              {fo.proto === "icmp" ? (
                <IcmpTypeCodeFields type={fo.icmp_type} code={fo.icmp_code} onType={seto("icmp_type")} onCode={seto("icmp_code")} />
              ) : (
                <Field label="Port" help="Leave blank for all"><Input value={fo.port} onChange={seto("port")} placeholder="443" type="number" /></Field>
              )}
              <Field label="Action"><Select value={fo.action} onChange={seto("action")} options={ACTION_OPTS} /></Field>
              <Field label="Description"><Input value={fo.description} onChange={seto("description")} placeholder="Block IoT internet" /></Field>
            </div>
            <ErrMsg msg={err} />
            <Btn onClick={submitOutbound} disabled={busy}>{busy ? "Adding…" : "Add Rule"}</Btn>
          </Card>
        </>
      )}
    </>
  );
}

// ── Tailscale tab ─────────────────────────────────────────────────────────────