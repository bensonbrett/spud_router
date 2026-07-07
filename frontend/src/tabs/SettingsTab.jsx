// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState, useEffect, useRef } from "react";
import { GET, POST, DELETE } from "../api.js";
import { Btn, Card, CodeBlock, ErrMsg, Field, Input, OkMsg } from "../components/index.js";
import styles from "./SettingsTab.module.css";

const RESTART_POLL_MS = 2000;
const RESTART_POLL_MAX = 30; // ~60s

function ApiKeysCard({ showToast }) {
  const [keys, setKeys] = useState([]);
  const [keysErr, setKeysErr] = useState("");
  const [loadErr, setLoadErr] = useState("");
  const [newName, setNewName] = useState("");
  const [newScopes, setNewScopes] = useState(["read"]);
  const [createErr, setCreateErr] = useState("");
  const [createdKey, setCreatedKey] = useState(null);
  const [copiedKey, setCopiedKey] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [revokeId, setRevokeId] = useState(null);
  const [revokeErr, setRevokeErr] = useState("");

  const SCOPES = ["read", "write", "apply", "diagnostics", "vpn"];

  const loadKeys = () => {
    GET("/api/api-keys")
      .then((d) => { setKeys(d); setLoadErr(""); })
      .catch((e) => setLoadErr(e.message));
  };

  useEffect(() => { loadKeys(); }, []);

  const toggleScope = (scope) => {
    if (newScopes.includes(scope)) {
      if (newScopes.length > 1) {
        setNewScopes(newScopes.filter(s => s !== scope));
      }
    } else {
      setNewScopes([...newScopes, scope]);
    }
  };

  const copyKey = async (key) => {
    try {
      await navigator.clipboard.writeText(key);
      setCopiedKey(true);
      setTimeout(() => setCopiedKey(false), 2000);
    } catch (err) {
      console.error("Failed to copy:", err);
    }
  };

  const createKey = async () => {
    if (!newName.trim()) { setCreateErr("Name is required."); return; }
    if (newScopes.length === 0) { setCreateErr("At least one scope must be selected."); return; }
    setCreateBusy(true); setCreateErr(""); setCreatedKey(null);
    try {
      const result = await POST("/api/api-keys", { name: newName.trim(), scopes: newScopes });
      setCreatedKey(result);
      setNewName("");
      setNewScopes(["read"]);
      loadKeys();
      showToast("API key created — copy it now, it won't be shown again");
    } catch (e) { setCreateErr(e.message); }
    finally { setCreateBusy(false); }
  };

  const revokeKey = async () => {
    if (!revokeId) return;
    try {
      await DELETE(`/api/api-keys/${revokeId}`);
      setRevokeId(null);
      loadKeys();
      showToast("API key revoked");
    } catch (e) { setRevokeErr(e.message); }
  };

  const formatDate = (ts) => {
    if (!ts) return "Never";
    return new Date(ts * 1000).toLocaleString();
  };

  return (
    <Card title="API Keys">
      {loadErr && <ErrMsg msg={loadErr} />}

      {keys.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <p className={styles.settingsBackupDesc}>Active API keys (hashes are never stored or shown):</p>
          <table className={styles.keysTable}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Scope</th>
                <th>Created</th>
                <th>Last Used</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id}>
                  <td>{k.name}</td>
                  <td><code>{k.scopes.join(", ")}</code></td>
                  <td>{formatDate(k.created_at)}</td>
                  <td>{formatDate(k.last_used)}</td>
                  <td>
                    {revokeId === k.id ? (
                      <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <Btn variant="danger" onClick={revokeKey} disabled={!revokeId}>Confirm</Btn>
                        <Btn variant="ghost" onClick={() => setRevokeId(null)}>Cancel</Btn>
                      </span>
                    ) : (
                      <Btn variant="ghost" onClick={() => setRevokeId(k.id)}>Revoke</Btn>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <ErrMsg msg={revokeErr} />
        </div>
      )}

      {keys.length === 0 && !loadErr && (
        <p className={styles.settingsBackupDesc}>No API keys yet. Create one below to allow programmatic access.</p>
      )}

      <div className={styles.settingsBackupGrid}>
        <div>
          <p className={styles.settingsBackupDesc}>Create a new API key. The plaintext key is shown once — copy it immediately.</p>
          <Field label="Name"><Input value={newName} onChange={setNewName} placeholder="MCP server key" /></Field>
          <Field label="Scopes">
            <div className={styles.checkboxGroup}>
              {SCOPES.map((scope) => (
                <div key={scope} className={styles.checkboxRow}>
                  <input
                    type="checkbox"
                    id={`scope-${scope}`}
                    checked={newScopes.includes(scope)}
                    onChange={() => toggleScope(scope)}
                  />
                  <label htmlFor={`scope-${scope}`}>{scope}</label>
                </div>
              ))}
            </div>
          </Field>
          <ErrMsg msg={createErr} />
          <Btn onClick={createKey} disabled={createBusy || !newName.trim()}>
            {createBusy ? "Creating…" : "Create API Key"}
          </Btn>
        </div>
        <div>
          {createdKey && (
            <div>
              <p className={styles.settingsBackupDesc}><strong>New API key — copy now, it won't be shown again:</strong></p>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <CodeBlock content={createdKey.key} />
                <Btn variant="ghost" onClick={() => copyKey(createdKey.key)} disabled={copiedKey}>
                  {copiedKey ? "✓ Copied" : "Copy"}
                </Btn>
              </div>
              <OkMsg msg={`ID: ${createdKey.id} · Scopes: ${createdKey.scopes.join(", ")}`} />
              <Btn variant="ghost" onClick={() => setCreatedKey(null)}>Dismiss</Btn>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function McpCard({ showToast }) {
  const [status, setStatus] = useState(null);
  const [config, setConfig] = useState(null);
  const [statusErr, setStatusErr] = useState("");
  const [newKey, setNewKey] = useState(null);
  const [enableBusy, setEnableBusy] = useState(false);
  const [enableErr, setEnableErr] = useState("");
  const [copiedMcp, setCopiedMcp] = useState(false);

  const load = () => {
    GET("/api/mcp/status")
      .then((d) => { setStatus(d); setStatusErr(""); })
      .catch((e) => setStatusErr(e.message));
    GET("/api/mcp/config")
      .then((d) => setConfig(d))
      .catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const enableMcp = async () => {
    setEnableBusy(true); setEnableErr(""); setNewKey(null);
    try {
      const result = await POST("/api/mcp/enable");
      setNewKey(result);
      load();
      showToast("API key generated — copy it now, it won't be shown again");
    } catch (e) { setEnableErr(e.message); }
    finally { setEnableBusy(false); }
  };

  const cmd = newKey ? `spud-router-mcp --api-key ${newKey.key} --base-url https://192.168.10.1:8080`
    : config?.configured
      ? `spud-router-mcp --api-key <key-from-api-keys-tab> --base-url https://192.168.10.1:8080`
      : null;

  const copyCmd = async () => {
    if (!cmd) return;
    try {
      await navigator.clipboard.writeText(cmd);
      setCopiedMcp(true);
      setTimeout(() => setCopiedMcp(false), 2000);
    } catch (err) { /* ignore */ }
  };

  return (
    <Card title="🤖 AI Agent Setup">
      {statusErr && <ErrMsg msg={statusErr} />}

      {!newKey && !status?.configured ? (
        <div>
          <p className={styles.settingsBackupDesc}>
            Connect AI agents (Claude Desktop, OpenCode, VS Code Cline, GitHub Copilot)
            directly to your router. The MCP server runs on <strong>your machine</strong>
            and authenticates via API key — no SSH needed.
          </p>
          <Btn onClick={enableMcp} disabled={enableBusy}>
            {enableBusy ? "Generating…" : "Generate API Key"}
          </Btn>
          <ErrMsg msg={enableErr} />
        </div>
      ) : null}

      {newKey ? (
        <div>
          <p className={styles.settingsBackupDesc}>
            <strong>API Key created — copy this, it won't be shown again:</strong>
          </p>
          <CodeBlock content={newKey.key} />

          <p className={styles.settingsBackupDesc}>
            <strong>Run this command on your machine:</strong>
          </p>
          <div style={{ display: "flex", gap: 8, alignItems: "start" }}>
            <CodeBlock content={cmd} />
            <Btn variant="ghost" onClick={copyCmd} disabled={copiedMcp}>
              {copiedMcp ? "✓ Copied" : "Copy"}
            </Btn>
          </div>

          <p className={styles.settingsBackupDesc}>
            <strong>Or add to your MCP client config</strong> (OpenCode, Claude Desktop, Copilot, etc.):
          </p>
          <CodeBlock content={`{
  "mcpServers": {
    "spud-router": {
      "command": "spud-router-mcp",
      "args": ["--api-key", "${newKey.key}", "--base-url", "https://192.168.10.1:8080"]
    }
  }
}`} />
          <div style={{ marginTop: 8 }}>
            <Btn variant="ghost" onClick={() => setNewKey(null)}>Dismiss</Btn>
          </div>
        </div>
      ) : null}

      {status?.configured && !newKey ? (
        <div>
          <p className={styles.settingsBackupDesc}>
            <strong>✓ Configured</strong>
            {config?.api_key_id && <> · Key ID: <code>{config.api_key_id}</code></>}
          </p>
          <p className={styles.settingsBackupDesc}>
            An API key is configured. Create additional keys in the <strong>API Keys</strong> card above
            if needed. Use the key ID <code>{config?.api_key_id}</code> or any other API key with the CLI.
          </p>
          <p className={styles.settingsBackupDesc}>
            Install the CLI: <code>pip install git+https://github.com/bensonbrett/spud_router.git</code>
          </p>
          <p className={styles.settingsBackupDesc}>
            Then on your machine: <code>spud-router-mcp --api-key &lt;your-key&gt; --base-url https://192.168.10.1:8080</code>
          </p>
        </div>
      ) : null}
    </Card>
  );
}

function TlsCard({ showToast }) {
  const [info, setInfo] = useState(null);
  const [infoErr, setInfoErr] = useState("");
  const [certPem, setCertPem] = useState("");
  const [keyPem, setKeyPem] = useState("");
  const [uploadErr, setUploadErr] = useState("");
  const [uploadBusy, setUploadBusy] = useState(false);
  const [regenCn, setRegenCn] = useState("spud-router");
  const [regenSan, setRegenSan] = useState("");
  const [regenBusy, setRegenBusy] = useState(false);
  const [restartMsg, setRestartMsg] = useState(null); // { state, message }
  const pollRef = useRef(0);

  const loadInfo = () => {
    GET("/api/system/tls")
      .then((d) => { setInfo(d); setInfoErr(""); })
      .catch((e) => setInfoErr(e.message));
  };

  useEffect(() => { loadInfo(); }, []);

  const pollRestartStatus = () => {
    pollRef.current = 0;
    const tick = () => {
      pollRef.current += 1;
      GET("/api/system/tls/restart-status")
        .then((s) => {
          if (s.state === "restarting" || s.state === "none") {
            if (pollRef.current < RESTART_POLL_MAX) setTimeout(tick, RESTART_POLL_MS);
            else setRestartMsg({ state: "unknown", message: "Timed out waiting for the restart to finish — check connectivity." });
          } else {
            setRestartMsg(s);
            if (s.state === "ok") loadInfo();
          }
        })
        .catch(() => {
          // Service is mid-restart — connection refused is expected; keep polling.
          if (pollRef.current < RESTART_POLL_MAX) setTimeout(tick, RESTART_POLL_MS);
        });
    };
    setTimeout(tick, RESTART_POLL_MS);
  };

  const upload = async () => {
    setUploadBusy(true); setUploadErr(""); setRestartMsg(null);
    try {
      await POST("/api/system/tls", { cert_pem: certPem, key_pem: keyPem });
      setRestartMsg({ state: "restarting", message: "Restarting to activate the new certificate…" });
      showToast("Uploading certificate — service is restarting");
      pollRestartStatus();
      setCertPem(""); setKeyPem("");
    } catch (e) {
      setUploadErr(e.message);
    } finally {
      setUploadBusy(false);
    }
  };

  const regenerate = async () => {
    setRegenBusy(true); setUploadErr(""); setRestartMsg(null);
    try {
      const san = regenSan.split(",").map((s) => s.trim()).filter(Boolean);
      await POST("/api/system/tls/regenerate", { common_name: regenCn, san });
      setRestartMsg({ state: "restarting", message: "Restarting to activate the new certificate…" });
      showToast("Regenerating certificate — service is restarting");
      pollRestartStatus();
    } catch (e) {
      setUploadErr(e.message);
    } finally {
      setRegenBusy(false);
    }
  };

  return (
    <Card title="TLS Certificate">
      {infoErr && <ErrMsg msg={infoErr} />}
      {info && (
        <div className={styles.settingsBackupGrid}>
          <div>
            <p className={styles.settingsBackupDesc}><strong>Subject:</strong> {info.subject}</p>
            <p className={styles.settingsBackupDesc}><strong>Issuer:</strong> {info.issuer}</p>
            <p className={styles.settingsBackupDesc}>
              <strong>Expires:</strong> {info.not_after} {info.expired && <span style={{ color: "var(--color-danger)" }}>(EXPIRED)</span>}
            </p>
            {info.san?.length > 0 && <p className={styles.settingsBackupDesc}><strong>SAN:</strong> {info.san.join(", ")}</p>}
            <p className={styles.settingsBackupDesc}><strong>SHA-256 fingerprint:</strong></p>
            <CodeBlock content={info.fingerprint_sha256} />
          </div>
        </div>
      )}

      {restartMsg && (
        restartMsg.state === "ok" ? <OkMsg msg={restartMsg.message} />
        : restartMsg.state === "restarting" ? <p className={styles.settingsSessionDesc}>⏳ {restartMsg.message}</p>
        : <ErrMsg msg={restartMsg.message} />
      )}

      <div className={styles.settingsBackupGrid}>
        <div>
          <p className={styles.settingsBackupDesc}>Upload a cert + key (PEM). Validated before anything is written or restarted; if the new pair fails to come up, the previous one is restored automatically.</p>
          <Field label="Certificate (PEM)">
            <textarea className={styles.pasteArea} rows={6} value={certPem} onChange={(e) => setCertPem(e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
          </Field>
          <Field label="Private Key (PEM)">
            <textarea className={styles.pasteArea} rows={6} value={keyPem} onChange={(e) => setKeyPem(e.target.value)} placeholder="-----BEGIN PRIVATE KEY-----" />
          </Field>
          <ErrMsg msg={uploadErr} />
          <Btn onClick={upload} disabled={uploadBusy || !certPem || !keyPem}>{uploadBusy ? "Uploading…" : "Upload & Restart"}</Btn>
        </div>
        <div>
          <p className={styles.settingsBackupDesc}>Or regenerate a fresh self-signed certificate.</p>
          <Field label="Common Name"><Input value={regenCn} onChange={setRegenCn} placeholder="spud-router" /></Field>
          <Field label="Extra SANs" help="Comma-separated IPs/hostnames"><Input value={regenSan} onChange={setRegenSan} placeholder="192.168.1.1, spud-router.lan" /></Field>
          <Btn variant="ghost" onClick={regenerate} disabled={regenBusy}>{regenBusy ? "Generating…" : "Regenerate Self-Signed"}</Btn>
        </div>
      </div>
    </Card>
  );
}

export function SettingsTab({ onLogout, onImport, showToast }) {
  const [cur, setCur] = useState(""); const [nw, setNw] = useState(""); const [nw2, setNw2] = useState("");
  const [pwMsg, setPwMsg] = useState(""); const [pwErr, setPwErr] = useState(""); const [pwBusy, setPwBusy] = useState(false);
  const [impErr, setImpErr] = useState(""); const [impMsg, setImpMsg] = useState(""); const [impBusy, setImpBusy] = useState(false);
  const [confirmingReboot, setConfirmingReboot] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  const [rebootErr, setRebootErr] = useState("");

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

  const handleReboot = async () => {
    setRebootErr("");
    try {
      await POST("/api/system/reboot");
      setRebooting(true);
      setConfirmingReboot(false);
      if (showToast) showToast("Rebooting…");
    } catch (e) {
      setRebootErr(e.message);
    }
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

      <ApiKeysCard showToast={showToast} />

      <McpCard showToast={showToast} />

      <Card title="System">
        {rebooting ? (
          <p className={styles.settingsSessionDesc}>
            ⚠ Rebooting… the device will drop offline for ~1–2 minutes. Reconnect once it comes back up.
          </p>
        ) : confirmingReboot ? (
          <>
            <p className={styles.rebootWarning}>
              ⚠ This reboots the device. It will be unreachable for ~1–2 minutes. If you're remote,
              make sure you have another way back in (Tailscale SSH) before proceeding.
            </p>
            <div className={styles.rebootActions}>
              <Btn variant="danger" onClick={handleReboot}>Yes, reboot now</Btn>
              <Btn variant="ghost" onClick={() => setConfirmingReboot(false)}>Cancel</Btn>
            </div>
          </>
        ) : (
          <>
            <p className={styles.settingsSessionDesc}>Reboot the device remotely.</p>
            <Btn variant="danger" onClick={() => setConfirmingReboot(true)}>Reboot Device</Btn>
          </>
        )}
        <ErrMsg msg={rebootErr} />
      </Card>

      <TlsCard showToast={showToast} />

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
