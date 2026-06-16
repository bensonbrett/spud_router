import { useState, useEffect } from "react";
import { GET, POST } from "../api.js";
import { Btn, Card, Field, Input, Pill, Row, Toggle } from "../components/index.js";
import styles from "./TailscaleTab.module.css";
import sharedStyles from "./shared.module.css";

export function TailscaleTab({ state, onReload, showToast }) {
  const [f, setF] = useState(state?.tailscale || { enabled: false, advertise_routes: [], exit_node: false, accept_routes: true });
  const [input, setInput] = useState("");
  const [tsLive, setTsLive] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => setF(state?.tailscale || { enabled: false, advertise_routes: [], exit_node: false, accept_routes: true }), [state]);
  useEffect(() => { GET("/api/tailscale/status").then(setTsLive).catch(() => {}); }, []);

  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));
  const addRoute = () => {
    if (input && !f.advertise_routes.includes(input)) {
      set("advertise_routes")([...f.advertise_routes, input]);
      setInput("");
    }
  };
  const save = async () => {
    await POST("/api/tailscale", f); onReload(); showToast("Tailscale saved");
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

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

      <Card title="Configuration">
        <div className={styles.mb16}>
          <Toggle value={f.enabled} onChange={set("enabled")} label="Enable Tailscale" />
        </div>
        <div className={styles.tsConfig} data-disabled={!f.enabled}>
          <div className={sharedStyles.toggleRow}>
            <Toggle value={f.accept_routes} onChange={set("accept_routes")} label="Accept routes from Tailnet" />
            <Toggle value={f.exit_node}     onChange={set("exit_node")}     label="Advertise as exit node" />
          </div>
          <Field label="Advertised Routes">
            <div className={styles.routeAddRow}>
              <Input value={input} onChange={setInput} placeholder="192.168.10.0/24" onKeyDown={(e) => e.key === "Enter" && addRoute()} />
              <button className={styles.routeAddBtn} onClick={addRoute}>+ Add</button>
            </div>
            <div className={styles.routeTagList}>
              {f.advertise_routes.map((r) => (
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