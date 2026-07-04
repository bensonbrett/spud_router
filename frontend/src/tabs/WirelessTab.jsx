// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState, useEffect } from "react";
import { GET, POST, DELETE } from "../api.js";
import { Btn, Card, ErrMsg, Field, Input, Pill, Row, Select, Toggle } from "../components/index.js";
import styles from "./WirelessTab.module.css";

const BAND_OPTS = [
  { value: "2.4", label: "2.4 GHz" },
  { value: "5",   label: "5 GHz"   },
];

const CHANNEL_OPTS_24 = [
  { value: "auto", label: "Auto" },
  ...["1","2","3","4","5","6","7","8","9","10","11"].map(c => ({ value: c, label: `Channel ${c}` })),
];

const CHANNEL_OPTS_5 = [
  { value: "auto", label: "Auto" },
  ...["36","40","44","48","52","56","60","64","100","104","108","112","116","132","136","140","149","153","157","161","165"]
    .map(c => ({ value: c, label: `Channel ${c}` })),
];

const SECURITY_OPTS = [
  { value: "wpa2",   label: "WPA2-PSK"           },
  { value: "wpa3",   label: "WPA3-SAE"            },
  { value: "wpa2/3", label: "WPA2/WPA3 (mixed)"   },
  { value: "open",   label: "Open (no encryption)" },
];

const defSsid = {
  ssid: "", vlan_id: "", band: "2.4", channel: "auto",
  security: "wpa2", password: "", hidden: false, enabled: true,
};

export function WirelessTab({ state, onReload, showToast }) {
  const [wireless,     setWireless]     = useState(null);
  const [ifaces,       setIfaces]       = useState([]);
  const [form,         setForm]         = useState(defSsid);
  const [showPass,     setShowPass]     = useState(false);
  const [editingId,    setEditingId]    = useState(null);
  const [err,          setErr]          = useState("");
  const [busy,         setBusy]         = useState(false);
  const [globalSaved,  setGlobalSaved]  = useState(false);

  useEffect(() => {
    GET("/api/wireless").then(setWireless).catch(() => {});
    GET("/api/wireless/interfaces").then(setIfaces).catch(() => {});
  }, []);

  const set  = k => v => setForm(p => ({ ...p, [k]: v }));
  const setW = k => v => setWireless(p => ({ ...p, [k]: v }));

  const vlans     = state?.vlans || [];
  const vlanOpts  = vlans.map(v => ({ value: String(v.vlan_id), label: `VLAN ${v.vlan_id} — ${v.name}` }));
  const ifaceOpts = ifaces.length
    ? ifaces.map(i => ({ value: i.name, label: `${i.name}${i.supports_ap ? "" : " ⚠ no AP mode"}` }))
    : [{ value: "wlan0", label: "wlan0" }];

  // Active interface capability info
  const activeIface = ifaces.find(i => i.name === wireless?.interface);
  const noApSupport = activeIface && !activeIface.supports_ap;
  const maxVaps     = activeIface?.max_vaps ?? 0;
  const ssids       = wireless?.ssids || [];
  const overVapLimit = maxVaps > 0 && ssids.length >= maxVaps;

  async function saveGlobal() {
    setBusy(true); setErr("");
    try {
      await POST("/api/wireless", wireless);
      setGlobalSaved(true);
      setTimeout(() => setGlobalSaved(false), 2000);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function addSsid() {
    if (!form.ssid)     { setErr("SSID name required."); return; }
    if (!form.vlan_id)  { setErr("VLAN required."); return; }
    if (form.security !== "open" && form.password.length < 8) {
      setErr("Password must be at least 8 characters.");
      return;
    }
    setBusy(true); setErr("");
    try {
      await POST("/api/wireless/ssids", { ...form, vlan_id: parseInt(form.vlan_id) });
      const updated = await GET("/api/wireless");
      setWireless(updated);
      setForm(defSsid);
      showToast("SSID added. Click Apply to go live.");
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function updateSsid() {
    if (form.security !== "open" && form.password.length < 8) {
      setErr("Password must be at least 8 characters.");
      return;
    }
    setBusy(true); setErr("");
    try {
      await POST(`/api/wireless/ssids/${editingId}`, {
        ...form,
        id: editingId,
        vlan_id: parseInt(form.vlan_id),
      });
      const updated = await GET("/api/wireless");
      setWireless(updated);
      setForm(defSsid);
      setEditingId(null);
      showToast("SSID updated. Click Apply to go live.");
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function deleteSsid(id) {
    try {
      await DELETE(`/api/wireless/ssids/${id}`);
      const updated = await GET("/api/wireless");
      setWireless(updated);
    } catch (e) { setErr(e.message); }
  }

  function startEdit(ssid) {
    setEditingId(ssid.id);
    setForm({ ...ssid, vlan_id: String(ssid.vlan_id) });
    setErr("");
  }

  function cancelEdit() {
    setEditingId(null);
    setForm(defSsid);
    setErr("");
  }

  if (!wireless) {
    return <div className={styles.loading}>Loading wireless config…</div>;
  }

  return (
    <>
      {/* Global wireless settings */}
      <Card title="Wireless AP">
        <div className={styles.enableRow}>
          <Toggle value={!!wireless.enabled} onChange={setW("enabled")} label="Enable wireless access point" />
          {wireless.enabled && ssids.length > 0 && (
            <Pill variant="success">{ssids.filter(s => s.enabled).length} SSID{ssids.length !== 1 ? "s" : ""} active</Pill>
          )}
        </div>

        {/* Hardware capability warnings */}
        {noApSupport && (
          <div className={styles.warnBox}>
            ⚠ <strong>{wireless.interface}</strong> does not support AP mode. Most USB WiFi adapters
            require a specific driver. Check: <code>iw list | grep -A5 "interface modes"</code>
          </div>
        )}
        {activeIface && maxVaps > 0 && (
          <div className={styles.infoBox}>
            ℹ {activeIface.name} supports up to {maxVaps} simultaneous SSID{maxVaps !== 1 ? "s" : ""}.
            {activeIface.driver && ` Driver: ${activeIface.driver}`}
          </div>
        )}
        {!activeIface && ifaces.length > 0 && (
          <div className={styles.infoBox}>
            ℹ No wireless interfaces detected yet. Plug in your USB WiFi adapter and click Reload.
          </div>
        )}

        <div className={styles.globalFields} data-disabled={!wireless.enabled}>
          <div className={styles.grid3}>
            <Field label="Interface">
              <Select value={wireless.interface || "wlan0"} onChange={setW("interface")} options={ifaceOpts} />
            </Field>
            <Field label="Country Code" help="Required for 5 GHz and DFS channels">
              <Input value={wireless.country_code || "US"} onChange={setW("country_code")} placeholder="US" maxLength={2} />
            </Field>
          </div>
        </div>

        <div className={styles.actions}>
          <Btn onClick={saveGlobal} disabled={busy || !wireless.enabled}>
            {globalSaved ? "✓ Saved" : "Save"}
          </Btn>
          <Btn variant="ghost" small onClick={() => GET("/api/wireless/interfaces").then(setIfaces)}>
            ↻ Reload interfaces
          </Btn>
        </div>
      </Card>

      {/* SSID list */}
      {wireless.enabled && (
        <Card title={`SSIDs (${ssids.length})`}>
          {ssids.length === 0 && (
            <p className={styles.emptyState}>No SSIDs configured. Add one below.</p>
          )}
          {ssids.map(s => {
            const vlan = vlans.find(v => v.vlan_id === s.vlan_id);
            return (
              <Row
                key={s.id}
                left={
                  <>
                    <span className={styles.ssidName}>{s.ssid}</span>
                    {s.hidden && <span className={styles.hiddenBadge}>hidden</span>}
                  </>
                }
                sub={`VLAN ${s.vlan_id}${vlan ? ` (${vlan.name})` : ""} · ${s.band} GHz · ${s.security} · ch ${s.channel}`}
                badges={[
                  <Pill key="sec" variant={s.security === "open" ? "warning" : "success"}>
                    {s.security}
                  </Pill>,
                  !s.enabled && <Pill key="dis" variant="muted">disabled</Pill>,
                ].filter(Boolean)}
                right={
                  <div className={styles.rowBtns}>
                    <Btn variant="ghost" small onClick={() => startEdit(s)}>Edit</Btn>
                    <Btn variant="danger" small onClick={() => deleteSsid(s.id)}>✕</Btn>
                  </div>
                }
              />
            );
          })}
        </Card>
      )}

      {/* Add / Edit SSID form */}
      {wireless.enabled && (!overVapLimit || editingId) && (
        <Card title={editingId ? "Edit SSID" : "Add SSID"}>
          {overVapLimit && editingId && (
            <div className={styles.infoBox}>
              ℹ Editing existing SSID — hardware VAP limit reached, no new SSIDs can be added.
            </div>
          )}

          <div className={styles.grid3}>
            <Field label="SSID Name" help="Network name (max 32 chars)">
              <Input value={form.ssid} onChange={set("ssid")} placeholder="HomeNet" maxLength={32} />
            </Field>
            <Field label="VLAN" help="Clients join this VLAN">
              <Select
                value={String(form.vlan_id)}
                onChange={set("vlan_id")}
                options={[{ value: "", label: "Select a VLAN…" }, ...vlanOpts]}
              />
            </Field>
            <Field label="Band">
              <Select value={form.band} onChange={v => { set("band")(v); set("channel")("auto"); }} options={BAND_OPTS} />
            </Field>
            <Field label="Channel">
              <Select
                value={form.channel}
                onChange={set("channel")}
                options={form.band === "5" ? CHANNEL_OPTS_5 : CHANNEL_OPTS_24}
              />
            </Field>
            <Field label="Security">
              <Select value={form.security} onChange={set("security")} options={SECURITY_OPTS} />
            </Field>
            {form.security !== "open" && (
              <Field label="Password" help="Min 8 characters">
                <div className={styles.passwordRow}>
                  <Input
                    value={form.password}
                    onChange={set("password")}
                    type={showPass ? "text" : "password"}
                    placeholder="Min 8 characters"
                  />
                  <button className={styles.showPassBtn} onClick={() => setShowPass(p => !p)}>
                    {showPass ? "Hide" : "Show"}
                  </button>
                </div>
              </Field>
            )}
          </div>

          <div className={styles.toggleRow}>
            <Toggle value={!!form.hidden}  onChange={set("hidden")}  label="Hidden SSID (don't broadcast)" />
            <Toggle value={!!form.enabled} onChange={set("enabled")} label="Enabled" />
          </div>

          <ErrMsg msg={err} />

          <div className={styles.formActions}>
            {editingId ? (
              <>
                <Btn onClick={updateSsid} disabled={busy}>{busy ? "Saving…" : "Save Changes"}</Btn>
                <Btn variant="ghost" onClick={cancelEdit}>Cancel</Btn>
              </>
            ) : (
              <Btn onClick={addSsid} disabled={busy}>{busy ? "Adding…" : "Add SSID"}</Btn>
            )}
          </div>
        </Card>
      )}

      {/* VAP limit reached */}
      {wireless.enabled && overVapLimit && !editingId && (
        <div className={styles.warnBox}>
          ⚠ Hardware VAP limit reached ({maxVaps} SSID{maxVaps !== 1 ? "s" : ""} maximum for {wireless.interface}).
          Remove an existing SSID to add a new one.
        </div>
      )}

      {/* How it works */}
      <Card title="How wireless bridging works">
        <p className={styles.explainerText}>
          Each SSID is bridged to its VLAN — wireless clients land on the same network
          segment as wired clients on that VLAN port. The same firewall rules, DHCP
          scope, and DNS entries apply to both.
        </p>
        <div className={styles.diagramWrap}>
          <div className={styles.diagram}>
            {ssids.map(s => {
              const vlan = vlans.find(v => v.vlan_id === s.vlan_id);
              return (
                <div key={s.id} className={styles.diagramRow}>
                  <span className={styles.diagramSsid}>📶 "{s.ssid}"</span>
                  <span className={styles.diagramArrow}>→</span>
                  <span className={styles.diagramBridge}>br-vlan{s.vlan_id}</span>
                  <span className={styles.diagramArrow}>→</span>
                  <span className={styles.diagramVlan}>
                    eth0.{s.vlan_id}{vlan ? ` (${vlan.name})` : ""}
                  </span>
                </div>
              );
            })}
            {ssids.length === 0 && (
              <span className={styles.diagramEmpty}>Add SSIDs above to see the bridge layout</span>
            )}
          </div>
        </div>
      </Card>
    </>
  );
}
