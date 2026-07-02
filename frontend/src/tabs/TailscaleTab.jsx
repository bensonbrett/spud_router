import { useState, useEffect } from "react";
import { GET, POST, DELETE } from "../api.js";
import { Btn, Card, ErrMsg, Field, Input, Pill, Row, Toggle } from "../components/index.js";
import styles from "./TailscaleTab.module.css";
import sharedStyles from "./shared.module.css";

export function TailscaleTab({ state, onReload, showToast }) {
  const [f, setF] = useState(state?.tailscale || { enabled: false, advertise_routes: [], exit_node: false, accept_routes: true });
  const [input, setInput] = useState("");
  const [tsLive, setTsLive] = useState(null);
  const [saved, setSaved] = useState(false);
  const [hasAuthKey, setHasAuthKey] = useState(false);
  const [authKeyInput, setAuthKeyInput] = useState("");
  const [authKeyBusy, setAuthKeyBusy] = useState(false);
  const [authKeyErr, setAuthKeyErr] = useState("");
  const [candidates, setCandidates] = useState([]);

  useEffect(() => setF(state?.tailscale || { enabled: false, advertise_routes: [], exit_node: false, accept_routes: true }), [state]);
  useEffect(() => { GET("/api/tailscale/status").then(setTsLive).catch(() => {}); }, []);
  useEffect(() => { refreshAuthKeyStatus(); }, []);
  useEffect(() => { GET("/api/tailscale/candidate-routes").then(setCandidates).catch(() => {}); }, []);

  const refreshAuthKeyStatus = () => {
    GET("/api/tailscale").then((cfg) => setHasAuthKey(!!cfg.has_auth_key)).catch(() => {});
  };

  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));
  const addRoute = () => {
    if (input && !f.advertise_routes.includes(input)) {
      set("advertise_routes")([...f.advertise_routes, input]);
      setInput("");
    }
  };
  const toggleCandidate = (cidr) => (checked) => {
    if (checked) {
      if (!f.advertise_routes.includes(cidr)) set("advertise_routes")([...f.advertise_routes, cidr]);
    } else {
      set("advertise_routes")(f.advertise_routes.filter((x) => x !== cidr));
    }
  };
  const save = async () => {
    await POST("/api/tailscale", f); onReload(); showToast("Tailscale saved");
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const saveAuthKey = async () => {
    setAuthKeyErr("");
    setAuthKeyBusy(true);
    try {
      await POST("/api/tailscale/authkey", { auth_key: authKeyInput });
      setAuthKeyInput("");
      refreshAuthKeyStatus();
      onReload();
      showToast("Auth key saved — connecting");
    } catch (e) {
      setAuthKeyErr(e.message);
    } finally {
      setAuthKeyBusy(false);
    }
  };

  const clearAuthKey = async () => {
    setAuthKeyErr("");
    setAuthKeyBusy(true);
    try {
      await DELETE("/api/tailscale/authkey");
      refreshAuthKeyStatus();
      showToast("Auth key cleared");
    } catch (e) {
      setAuthKeyErr(e.message);
    } finally {
      setAuthKeyBusy(false);
    }
  };

  const candidateCidrs = new Set(candidates.map((c) => c.cidr));
  const extraRoutes = f.advertise_routes.filter((r) => !candidateCidrs.has(r));

  const self  = tsLive?.Self;
  const peers = Object.values(tsLive?.Peer || {});

  return (
    <>
      {tsLive && !tsLive.error && (
        <Card title="Tailscale Status">
          <div className={styles.peerSelf}>
            <div className={styles.peerDot} data-online={self?.Online} />
            <span className={styles.peerSelfName}>{self?.DNSName}</span>
            <Pill variant="accent">this device</Pill>
          </div>
          <div className={styles.peerSelfIp}>{self?.TailscaleIPs?.join(", ")}</div>
          {peers.map((p, i) => (
            <Row
              key={i}
              left={p.DNSName}
              sub={p.TailscaleIPs?.join(", ")}
              badges={[
                <div key="dot" className={styles.peerDot} data-online={p.Online} />,
                <Pill key="s" variant={p.Online ? "success" : "muted"}>{p.Online ? "online" : "offline"}</Pill>,
              ]}
            />
          ))}
        </Card>
      )}
      {tsLive?.error === "tailscale not installed" && (
        <div className={styles.tsNotInstalled}>
          ⚠ Tailscale not installed. Run: <code>curl -fsSL https://tailscale.com/install.sh | sh</code>
        </div>
      )}

      <Card title="Auth Key">
        {hasAuthKey ? (
          <div className={styles.authKeyRow}>
            <Pill variant="success">✓ Auth key set</Pill>
            <Input
              value={authKeyInput}
              onChange={setAuthKeyInput}
              type="password"
              placeholder="tskey-auth-… (replace)"
            />
            <Btn onClick={saveAuthKey} disabled={authKeyBusy || !authKeyInput} small>Replace</Btn>
            <Btn onClick={clearAuthKey} disabled={authKeyBusy} variant="danger" small>Clear</Btn>
          </div>
        ) : (
          <div className={styles.authKeyRow}>
            <Input
              value={authKeyInput}
              onChange={setAuthKeyInput}
              type="password"
              placeholder="tskey-auth-…"
            />
            <Btn onClick={saveAuthKey} disabled={authKeyBusy || !authKeyInput} small>Save key</Btn>
          </div>
        )}
        <ErrMsg msg={authKeyErr} />
        <p className={styles.authKeyHelp}>
          Paste a pre-created, reusable, non-ephemeral auth key from the Tailscale admin console.
          Ephemeral keys will cause the router to disappear from your tailnet on restart.
        </p>
      </Card>

      <Card title="Configuration">
        <div className={styles.mb16}>
          <Toggle value={f.enabled} onChange={set("enabled")} label="Enable Tailscale" />
        </div>
        <div className={styles.tsConfig} data-disabled={!f.enabled}>
          <div className={sharedStyles.toggleRow}>
            <Toggle value={f.accept_routes} onChange={set("accept_routes")} label="Accept routes from Tailnet" />
            <Toggle value={f.exit_node}     onChange={set("exit_node")}     label="Advertise as exit node" />
          </div>
          <Field
            label="Advertised Routes"
            help="Advertised subnet routes must be approved in the Tailscale admin console before they take effect."
          >
            {candidates.length > 0 && (
              <div className={styles.candidateList}>
                {candidates.map((c) => (
                  <label key={c.cidr} className={styles.candidateRow}>
                    <input
                      type="checkbox"
                      checked={f.advertise_routes.includes(c.cidr)}
                      onChange={(e) => toggleCandidate(c.cidr)(e.target.checked)}
                    />
                    <span className={styles.candidateLabel}>{c.label}</span>
                    <span className={styles.candidateCidr}>{c.cidr}</span>
                  </label>
                ))}
              </div>
            )}
            <div className={styles.routeAddRow}>
              <Input value={input} onChange={setInput} placeholder="192.168.10.0/24" onKeyDown={(e) => e.key === "Enter" && addRoute()} />
              <button className={styles.routeAddBtn} onClick={addRoute}>+ Add</button>
            </div>
            <div className={styles.routeTagList}>
              {extraRoutes.map((r) => (
                <span key={r} className={styles.routeTag}>
                  {r}
                  <button className={styles.routeTagRemove} onClick={() => set("advertise_routes")(f.advertise_routes.filter((x) => x !== r))}>×</button>
                </span>
              ))}
              {f.advertise_routes.length === 0 && (
                <span className={styles.noRoutesText}>No routes advertised</span>
              )}
            </div>
          </Field>
        </div>
        <div className={styles.mt16}>
          <Btn onClick={save}>{saved ? "✓ Saved" : "Save"}</Btn>
        </div>
      </Card>
    </>
  );
}

// ── Status tab ────────────────────────────────────────────────────────────────