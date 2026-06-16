import { useState } from "react";
import { Btn, Card, ErrMsg, Field, Input, Pill, Row, Select } from "../components/index.js";
import styles from "./FirewallTab.module.css";
import sharedStyles from "./shared.module.css";
import { POST, DELETE } from "../api.js";

const defInbound   = { vlan_id: "0", proto: "tcp", port: "", action: "accept", description: "" };
const defIntervlan = { from_vlan: "0", to_vlan: "0", proto: "any", port: "", action: "accept", description: "" };


export function FirewallTab({ state, onReload, showToast }) {
  const [section, setSection] = useState("inbound");
  const [fi,  setFi]  = useState(defInbound);
  const [fiv, setFiv] = useState(defIntervlan);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const seti  = (k) => (v) => setFi((p)  => ({ ...p, [k]: v }));
  const setiv = (k) => (v) => setFiv((p) => ({ ...p, [k]: v }));

  const vlans    = state?.vlans || [];
  const fw_in    = state?.fw_inbound   || [];
  const fw_iv    = state?.fw_intervlan || [];
  const vlanOpts = [{ value: "0", label: "All VLANs" }, ...vlans.map((v) => ({ value: String(v.vlan_id), label: `VLAN ${v.vlan_id} — ${v.name}` }))];
  const vlanMap  = Object.fromEntries(vlans.map((v) => [v.vlan_id, v.name]));
  const vlanName = (id) => (id === 0 ? "All VLANs" : vlanMap[id] || `VLAN ${id}`);
  const protoPort = (r) => r.proto && r.proto !== "any" ? `${r.proto.toUpperCase()}${r.port ? `:${r.port}` : ""}` : "any";
  const hasIvRules = fw_iv.length > 0;

  const submitInbound = async () => {
    setBusy(true); setErr("");
    try {
      await POST("/api/firewall/inbound", { ...fi, vlan_id: parseInt(fi.vlan_id), port: fi.port ? parseInt(fi.port) : null });
      onReload();
      showToast("Inbound rule added");
      setFi(defInbound);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const submitIntervlan = async () => {
    setBusy(true); setErr("");
    try {
      await POST("/api/firewall/intervlan", { ...fiv, from_vlan: parseInt(fiv.from_vlan), to_vlan: parseInt(fiv.to_vlan), port: fiv.port ? parseInt(fiv.port) : null });
      onReload();
      showToast("Inter-VLAN rule added");
      setFiv(defIntervlan);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
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
        {[["inbound", "🛡 Inbound Rules", fw_in.length], ["intervlan", "↔ Inter-VLAN Rules", fw_iv.length]].map(([id, label, count]) => (
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
              <Field label="Port" help="Leave blank for all"><Input value={fi.port} onChange={seti("port")} placeholder="22" type="number" /></Field>
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
              <Field label="Port" help="Leave blank for all"><Input value={fiv.port} onChange={setiv("port")} placeholder="443" type="number" /></Field>
              <Field label="Description"><Input value={fiv.description} onChange={setiv("description")} placeholder="Trusted → IoT HTTPS" /></Field>
            </div>
            <ErrMsg msg={err} />
            <Btn onClick={submitIntervlan} disabled={busy}>{busy ? "Adding…" : "Add Rule"}</Btn>
          </Card>
        </>
      )}
    </>
  );
}

// ── Tailscale tab ─────────────────────────────────────────────────────────────