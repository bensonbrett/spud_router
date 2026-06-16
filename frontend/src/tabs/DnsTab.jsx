import { useState } from "react";
import { Btn, Card, ErrMsg, Field, Input, Row } from "../components/index.js";
import styles from "./shared.module.css";
import { POST, DELETE } from "../api.js";

const defDns = { hostname: "", ip: "", description: "" };


export function DnsTab({ state, onReload, showToast }) {
  const [f, setF] = useState(defDns);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));
  const entries = state?.dns_entries || [];
  const domain  = `${state?.router?.hostname || "spud-router"}.lan`;

  const submit = async () => {
    if (!f.hostname || !f.ip) { setErr("Hostname and IP required."); return; }
    setBusy(true);
    setErr("");
    try { await POST("/api/dns", f); setF(defDns); showToast(`${f.hostname} added`); }
    catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <Card
        title={`Local DNS Entries (${entries.length})`}
        right={<span className={styles.cardHeaderMeta}>domain: {domain}</span>}
      >
        {entries.length === 0 && <p className={styles.emptyState}>No custom entries yet.</p>}
        {entries.map((e) => (
          <Row
            key={e.hostname}
            left={
              <>
                <span className={styles.mono}>{e.hostname}</span>
                {e.description && <span className={styles.descriptionText}>— {e.description}</span>}
              </>
            }
            sub={`${e.hostname}.${domain}  →  ${e.ip}`}
            right={<Btn variant="danger" small onClick={async () => { await DELETE(`/api/dns/${encodeURIComponent(e.hostname)}`); onReload(); showToast(`${e.hostname} removed`); }}>✕</Btn>}
          />
        ))}
      </Card>

      <Card title="Add DNS Entry">
        <div className={styles.formGrid3}>
          <Field label="Hostname" help="Short name e.g. nas">
            <Input value={f.hostname} onChange={set("hostname")} placeholder="nas" />
          </Field>
          <Field label="IP Address">
            <Input value={f.ip} onChange={set("ip")} placeholder="192.168.10.10" />
          </Field>
          <Field label="Description">
            <Input value={f.description} onChange={set("description")} placeholder="TrueNAS" />
          </Field>
        </div>
        <ErrMsg msg={err} />
        <Btn onClick={submit} disabled={busy}>{busy ? "Adding…" : "Add Entry"}</Btn>
      </Card>
    </>
  );
}

// ── Routes tab ────────────────────────────────────────────────────────────────
const defRoute = { destination: "", gateway: "", interface: "", description: "" };
