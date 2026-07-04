// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState, useEffect } from "react";
import { GET, POST, PUT, DELETE } from "../api.js";
import { Btn, Card, CodeBlock, ErrMsg, Field, Input, Pill, ProviderSection, Row, Select, Toggle } from "../components/index.js";
import styles from "./VpnTab.module.css";
import sharedStyles from "./shared.module.css";

/**
 * VPN tab: a container of independent, collapsible provider sections.
 * Providers are enabled/configured completely independently of each
 * other (state.tailscale / state.wireguard / state.nebula each keep their
 * own `enabled` flag — there is deliberately no single "provider" selector)
 * and coexist on the router at the same time (see backend/apply_core.py's
 * VPN_PROVIDERS dispatch and generators/iptables.py's
 * VPN_PROVIDER_INTERFACES, both additive/stacked by design).
 *
 * Tailscale and WireGuard are fully wired below; Nebula lands in a later
 * release (#91) as its own ProviderSection with the same shape.
 */
function TailscaleSection({ state, onReload, showToast }) {
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

const WG_MODE_OPTIONS = [
  { value: "server", label: "Server (accept peer connections)" },
  { value: "client", label: "Client (dial out to a peer)" },
];

function downloadTextFile(filename, text) {
  const blob = document.createElement("a");
  blob.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  blob.download = filename;
  blob.click();
}

function WireGuardSection({ state, onReload, showToast }) {
  const wg = state?.wireguard || {};
  const [f, setF] = useState({
    enabled: wg.enabled || false,
    mode: wg.mode || "server",
    listen_port: wg.listen_port || 51820,
    address: wg.address || "",
    private_key: wg.private_key || "",
  });
  const [hasKey, setHasKey] = useState(!!wg.has_key);
  const [publicKey, setPublicKey] = useState(wg.public_key || "");
  const [peers, setPeers] = useState(wg.peers || []);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");
  const [regenBusy, setRegenBusy] = useState(false);

  const [newPeer, setNewPeer] = useState({ name: "", allowed_ips: "", endpoint: "", client_address: "", public_key: "" });
  const [peerMode, setPeerMode] = useState("generate"); // "generate" | "paste"
  const [peerErr, setPeerErr] = useState("");
  const [peerBusy, setPeerBusy] = useState(false);
  const [reveal, setReveal] = useState(null); // { name, private_key, client_config, qr_png_base64 }

  useEffect(() => {
    const w = state?.wireguard || {};
    setF({
      enabled: w.enabled || false,
      mode: w.mode || "server",
      listen_port: w.listen_port || 51820,
      address: w.address || "",
      private_key: w.private_key || "",
    });
    setHasKey(!!w.has_key);
    setPublicKey(w.public_key || "");
    setPeers(w.peers || []);
  }, [state]);

  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));

  const save = async () => {
    setErr("");
    setSaving(true);
    try {
      await PUT("/api/wireguard", f);
      onReload();
      showToast("WireGuard saved");
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  };

  const regenerateKey = async () => {
    setRegenBusy(true);
    setErr("");
    try {
      const resp = await POST("/api/wireguard/regenerate-key");
      setPublicKey(resp.public_key);
      setHasKey(true);
      onReload();
      showToast("New WireGuard key generated");
    } catch (e) {
      setErr(e.message);
    } finally {
      setRegenBusy(false);
    }
  };

  const addPeer = async () => {
    setPeerErr("");
    setPeerBusy(true);
    try {
      const body = {
        name: newPeer.name,
        allowed_ips: newPeer.allowed_ips.split(",").map((s) => s.trim()).filter(Boolean),
        endpoint: newPeer.endpoint || null,
      };
      if (peerMode === "paste") {
        body.public_key = newPeer.public_key;
      } else {
        body.client_address = newPeer.client_address;
      }
      const resp = await POST("/api/wireguard/peers", body);
      if (resp.private_key) {
        setReveal({
          name: newPeer.name,
          private_key: resp.private_key,
          client_config: resp.client_config,
          qr_png_base64: resp.qr_png_base64,
        });
      }
      setNewPeer({ name: "", allowed_ips: "", endpoint: "", client_address: "", public_key: "" });
      onReload();
      showToast("Peer added");
    } catch (e) {
      setPeerErr(e.message);
    } finally {
      setPeerBusy(false);
    }
  };

  const removePeer = async (id) => {
    await DELETE(`/api/wireguard/peers/${id}`);
    onReload();
    showToast("Peer removed");
  };

  return (
    <>
      <Card title="Configuration">
        <div className={sharedStyles.toggleRow}>
          <Toggle value={f.enabled} onChange={set("enabled")} label="Enable WireGuard" />
        </div>
        <div className={styles.tsConfig} data-disabled={!f.enabled}>
          <div className={sharedStyles.formGrid2}>
            <Field label="Mode">
              <Select value={f.mode} onChange={set("mode")} options={WG_MODE_OPTIONS} />
            </Field>
            <Field label="Listen Port" help="Server mode only — UDP port opened on the WAN interface.">
              <Input value={String(f.listen_port)} onChange={(v) => set("listen_port")(Number(v) || 0)} />
            </Field>
          </div>
          <Field label="Tunnel Address" help="This device's own address inside the WireGuard tunnel, e.g. 10.100.0.1/24">
            <Input value={f.address} onChange={set("address")} placeholder="10.100.0.1/24" />
          </Field>
          <Field label="Identity">
            <div className={styles.authKeyRow}>
              {hasKey ? <Pill variant="success">✓ Key set</Pill> : <Pill variant="muted">No key yet</Pill>}
              {publicKey && <span className={sharedStyles.mono}>{publicKey}</span>}
              <Btn onClick={regenerateKey} disabled={regenBusy} variant="danger" small>Regenerate key</Btn>
            </div>
            <p className={styles.authKeyHelp}>
              A key is generated automatically the first time WireGuard is enabled. Regenerating
              replaces this device's identity — existing peers will need the new public key.
            </p>
          </Field>
        </div>
        <ErrMsg msg={err} />
        <div className={styles.mt16}>
          <Btn onClick={save} disabled={saving}>{saved ? "✓ Saved" : "Save"}</Btn>
        </div>
      </Card>

      <Card title="Peers">
        {peers.length === 0 && <p className={sharedStyles.emptyState}>No peers configured.</p>}
        {peers.map((p) => (
          <Row
            key={p.id}
            left={p.name || p.public_key.slice(0, 12) + "…"}
            sub={`${p.allowed_ips.join(", ") || "no allowed IPs"}${p.endpoint ? "  ·  " + p.endpoint : ""}`}
            right={<Btn onClick={() => removePeer(p.id)} variant="danger" small>Remove</Btn>}
          />
        ))}

        <div className={styles.mt16}>
          <Field label="Peer name">
            <Input value={newPeer.name} onChange={(v) => setNewPeer((p) => ({ ...p, name: v }))} placeholder="phone" />
          </Field>
          <Field label="Allowed IPs" help="Comma-separated CIDRs this peer may use, e.g. 10.100.0.2/32">
            <Input
              value={newPeer.allowed_ips}
              onChange={(v) => setNewPeer((p) => ({ ...p, allowed_ips: v }))}
              placeholder="10.100.0.2/32"
            />
          </Field>
          <Field label="Endpoint" help="host:port — only needed if this device must dial out to the peer">
            <Input value={newPeer.endpoint} onChange={(v) => setNewPeer((p) => ({ ...p, endpoint: v }))} placeholder="" />
          </Field>

          <div className={sharedStyles.toggleRow}>
            <label className={styles.candidateRow}>
              <input type="radio" checked={peerMode === "generate"} onChange={() => setPeerMode("generate")} />
              <span>Generate a keypair for this peer</span>
            </label>
            <label className={styles.candidateRow}>
              <input type="radio" checked={peerMode === "paste"} onChange={() => setPeerMode("paste")} />
              <span>Paste the peer's own public key</span>
            </label>
          </div>

          {peerMode === "generate" ? (
            <Field label="Peer's tunnel address" help="Required so a client config can be generated for it, e.g. 10.100.0.2/32">
              <Input
                value={newPeer.client_address}
                onChange={(v) => setNewPeer((p) => ({ ...p, client_address: v }))}
                placeholder="10.100.0.2/32"
              />
            </Field>
          ) : (
            <Field label="Public key">
              <Input
                value={newPeer.public_key}
                onChange={(v) => setNewPeer((p) => ({ ...p, public_key: v }))}
                placeholder="44-character base64 key"
              />
            </Field>
          )}

          <ErrMsg msg={peerErr} />
          <div className={styles.mt16}>
            <Btn onClick={addPeer} disabled={peerBusy}>Add peer</Btn>
          </div>
        </div>
      </Card>

      {reveal && (
        <Card title={`New peer: ${reveal.name || "unnamed"}`}>
          <p className={styles.authKeyHelp}>
            This private key is shown once and is not stored by spud-router — save it now.
          </p>
          {reveal.qr_png_base64 && (
            <img className={styles.qrImage} src={reveal.qr_png_base64} alt="WireGuard client config QR code" />
          )}
          <CodeBlock content={reveal.client_config} />
          <div className={styles.mt16}>
            <Btn onClick={() => downloadTextFile(`${reveal.name || "wg-peer"}.conf`, reveal.client_config)} small>
              Download .conf
            </Btn>
            <Btn onClick={() => setReveal(null)} variant="ghost" small>Dismiss</Btn>
          </div>
        </Card>
      )}
    </>
  );
}

export function VpnTab({ state, onReload, showToast }) {
  const ts = state?.tailscale || {};
  const wg = state?.wireguard || {};

  return (
    <>
      <ProviderSection
        title="🔒 Tailscale"
        statusLine={ts.enabled ? "enabled" : "disabled"}
        defaultOpen
      >
        <TailscaleSection state={state} onReload={onReload} showToast={showToast} />
      </ProviderSection>

      <ProviderSection title="🔌 WireGuard" statusLine={wg.enabled ? "enabled" : "disabled"}>
        <WireGuardSection state={state} onReload={onReload} showToast={showToast} />
      </ProviderSection>

      <ProviderSection title="🌐 Nebula" statusLine="coming soon">
        <p className={sharedStyles.emptyState}>Nebula support is coming in a future release.</p>
      </ProviderSection>
    </>
  );
}
