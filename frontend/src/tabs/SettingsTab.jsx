import { useState } from "react";
import { POST, exportConfig } from "../api.js";
import { Btn, Card, CodeBlock, ErrMsg, Field, Input, OkMsg } from "../components/index.js";
import styles from "./SettingsTab.module.css";

export function SettingsTab({ onLogout, onImport }) {
  const [cur, setCur] = useState(""); const [nw, setNw] = useState(""); const [nw2, setNw2] = useState("");
  const [pwMsg, setPwMsg] = useState(""); const [pwErr, setPwErr] = useState(""); const [pwBusy, setPwBusy] = useState(false);
  const [impErr, setImpErr] = useState(""); const [impMsg, setImpMsg] = useState(""); const [impBusy, setImpBusy] = useState(false);

  const changePass = async () => {
    if (nw !== nw2) { setPwErr("Passwords don't match."); return; }
    if (nw.length < 8) { setPwErr("Minimum 8 characters."); return; }
    setPwBusy(true); setPwErr(""); setPwMsg("");
    try {
      await POST("/api/auth/change-password", { current_password: cur, new_password: nw });
      setPwMsg("Password changed."); setCur(""); setNw(""); setNw2("");
    } catch (e) { setPwErr(e.message); } finally { setPwBusy(false); }
  };

  const handleExport = async () => {
    const token = sessionStorage.getItem("spud_token") || "";
    const res = await fetch("/api/config/export", { headers: { "X-Session-Token": token } });
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const name = cd.match(/filename="([^"]+)"/)?.[1] || "spud-router-backup.zip";
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click();
  };

  const handleImport = async (e) => {
    const file = e.target.files?.[0]; if (!file) return;
    setImpBusy(true); setImpErr(""); setImpMsg("");
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const res = await POST("/api/config/import", data);
      setImpMsg(`Imported: ${res.vlans} VLANs · ${res.dns} DNS · ${res.routes} routes · ${res.fw_inbound} inbound · ${res.fw_intervlan} inter-VLAN`);
      if (onImport) onImport();
    } catch (e) { setImpErr(e.message); } finally { setImpBusy(false); e.target.value = ""; }
  };

  return (
    <>
      <Card title="Config Backup & Restore">
        <div className={styles.settingsBackupGrid}>
          <div>
            <p className={styles.settingsBackupDesc}>Export full config — VLANs, DNS, routes, firewall, Tailscale — as a zip archive.</p>
            <Btn variant="ghost" onClick={handleExport}>⬇ Export Config</Btn>
          </div>
          <div>
            <p className={styles.settingsBackupDesc}>Restore from a JSON backup. Click <strong>Apply</strong> after to push live.</p>
            <label className={styles.importFileLabel}>
              {impBusy ? "Importing…" : "⬆ Import Config"}
              <input type="file" accept=".json" onChange={handleImport} className={styles.srOnly} />
            </label>
            <ErrMsg msg={impErr} /><OkMsg msg={impMsg} />
          </div>
        </div>
      </Card>

      <Card title="Change Password">
        <div className={styles.passwordForm}>
          <Field label="Current Password"><Input value={cur} onChange={setCur} type="password" /></Field>
          <Field label="New Password"><Input value={nw} onChange={setNw} type="password" /></Field>
          <Field label="Confirm New Password"><Input value={nw2} onChange={setNw2} type="password" /></Field>
          <ErrMsg msg={pwErr} /><OkMsg msg={pwMsg} />
          <Btn onClick={changePass} disabled={pwBusy}>{pwBusy ? "Changing…" : "Change Password"}</Btn>
        </div>
      </Card>

      <Card title="Session">
        <p className={styles.settingsSessionDesc}>Sessions expire after 8 hours. Tokens reset on service restart.</p>
        <Btn variant="danger" onClick={onLogout}>Sign Out</Btn>
      </Card>

      <Card title="Install">
        <p className={styles.settingsInstallDesc}>On a fresh Armbian minimal Le Potato, extract the release tarball and run:</p>
        <CodeBlock content="sudo bash install.sh" />
      </Card>

      {import.meta.env.DEV && (
        <Card title="About">
          <p className={styles.settingsAbout}>Running in dev mode — connected to local backend at localhost:8080.</p>
        </Card>
      )}
    </>
  );
}

// ── Main App ──────────────────────────────────────────────────────────────────
const TABS = [
  { id: "vlans",     label: "VLANs",     icon: "⫿" },
  { id: "wan",       label: "WAN",       icon: "🌐" },
  { id: "dns",       label: "DNS",       icon: "◈"  },
  { id: "routes",    label: "Routes",    icon: "↗"  },
  { id: "firewall",  label: "Firewall",  icon: "🛡" },
  { id: "tailscale", label: "Tailscale", icon: "🔒" },
  { id: "status",    label: "Status",    icon: "◉"  },
  { id: "preview",   label: "Preview",   icon: "⟨⟩" },
  { id: "settings",  label: "Settings",  icon: "⚙"  },
];
