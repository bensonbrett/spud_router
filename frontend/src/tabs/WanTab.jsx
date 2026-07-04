// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState, useEffect } from "react";
import { GET, POST } from "../api.js";
import { Btn, Card, ErrMsg, Field, Input, Select, Toggle } from "../components/index.js";
import styles from "./WanTab.module.css";
import sharedStyles from "./shared.module.css";

export function WanTab({ state, interfaces, onReload, showToast }) {
  const [f, setF] = useState(state?.router || {});
  const [saved, setSaved] = useState(false);
  useEffect(() => setF(state?.router || {}), [state]);
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));
  const ifOpts = interfaces.map((i) => ({ value: i.name, label: i.name }));

  const save = async () => {
    await POST("/api/router", f); onReload(); showToast("WAN saved");
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <>
      <Card title="WAN & Router Settings">
        <div className={sharedStyles.formGrid3}>
          <Field label="Hostname">
            <Input value={f.hostname || ""} onChange={set("hostname")} placeholder="spud-router" />
          </Field>
          <Field label="WAN Interface">
            <Select value={f.wan_interface || ""} onChange={set("wan_interface")} options={ifOpts} />
          </Field>
          <Field label="WAN Mode">
            <Select value={f.wan_mode || "dhcp"} onChange={set("wan_mode")} options={[
              { value: "dhcp", label: "DHCP" }, { value: "static", label: "Static" },
            ]} />
          </Field>
          {f.wan_mode === "static" && (
            <>
              <Field label="WAN IP">
                <Input value={f.wan_ip || ""} onChange={set("wan_ip")} placeholder="203.0.113.5" />
              </Field>
              <Field label="Prefix">
                <Input value={f.wan_prefix || ""} onChange={set("wan_prefix")} type="number" placeholder="24" />
              </Field>
              <Field label="Gateway">
                <Input value={f.wan_gateway || ""} onChange={set("wan_gateway")} placeholder="203.0.113.1" />
              </Field>
            </>
          )}
          <Field label="DNS Source" help="Auto follows the WAN DHCP lease; Manual uses the servers below; DoH encrypts upstream queries">
            <Select value={f.wan_dns_mode || "auto"} onChange={set("wan_dns_mode")} options={[
              { value: "auto", label: "Auto (from WAN DHCP)" }, { value: "manual", label: "Manual" },
              { value: "doh", label: "DoH (encrypted)" },
            ]} />
          </Field>
          {(f.wan_dns_mode || "auto") === "manual" && (
            <>
              <Field label="Upstream DNS">
                <Input value={f.wan_dns || ""} onChange={set("wan_dns")} placeholder="1.1.1.1" />
              </Field>
              <Field label="Upstream DNS (secondary)">
                <Input value={f.wan_dns_alt || ""} onChange={set("wan_dns_alt")} placeholder="8.8.8.8" />
              </Field>
            </>
          )}
          {f.wan_dns_mode === "doh" && (
            <>
              <Field label="DoH Provider">
                <Select value={f.doh_provider || "cloudflare"} onChange={set("doh_provider")} options={[
                  { value: "cloudflare", label: "Cloudflare" },
                  { value: "quad9", label: "Quad9" },
                  { value: "google", label: "Google" },
                  { value: "custom", label: "Custom" },
                ]} />
              </Field>
              {f.doh_provider === "custom" && (
                <Field label="Custom DoH URL" help="Must be https://">
                  <Input value={f.doh_custom_url || ""} onChange={set("doh_custom_url")} placeholder="https://doh.example.com/dns-query" />
                </Field>
              )}
            </>
          )}
        </div>
        {f.wan_dns_mode === "doh" && (
          <div className={sharedStyles.toggleRow}>
            <Toggle value={!!f.block_wan_dns} onChange={set("block_wan_dns")} label="Block LAN plaintext DNS (port 53) to WAN" />
          </div>
        )}
        {f.wan_dns_mode === "doh" && f.block_wan_dns && (
          <div className={styles.mgmtWarnMsg}>
            ⚠ Devices with hardcoded DNS servers (not using this router's DHCP-assigned DNS) will
            lose DNS resolution. This is independent of DoH — DoH itself works without this toggle.
          </div>
        )}
        <div className={sharedStyles.formActions}>
          <Btn onClick={save}>{saved ? "✓ Saved" : "Save"}</Btn>
        </div>
      </Card>

      <Card title="Management Interface">
        <div className={styles.mgmtBox} data-enabled={!!f.mgmt_enabled}>
          <div className={styles.mgmtBoxInner}>
            <div className={styles.mgmtBoxText}>
              <div className={styles.mgmtBoxTitle}>Untagged access port</div>
              <p className={styles.mgmtBoxDesc}>
                Assigns an IP directly on the physical trunk interface so a laptop plugged in
                via ethernet (untagged) gets DHCP and can reach the web UI immediately —
                no switch config needed.
              </p>
              {f.mgmt_enabled && (
                <div className={styles.mgmtBoxHint}>
                  Connect a cable → get {f.mgmt_dhcp_start}–{f.mgmt_dhcp_end} → open http://{f.mgmt_ip}:8080
                </div>
              )}
            </div>
            <Toggle value={!!f.mgmt_enabled} onChange={set("mgmt_enabled")} label="Enable" />
          </div>
        </div>

        <div className={styles.mgmtBoxFields} data-disabled={!f.mgmt_enabled}>
          <div className={sharedStyles.formGrid3}>
            <Field label="Interface" help="Physical interface — usually the VLAN trunk">
              <Select value={f.mgmt_interface || "eth0"} onChange={set("mgmt_interface")} options={ifOpts} />
            </Field>
            <Field label="Router IP">
              <Input value={f.mgmt_ip || "192.168.1.1"} onChange={set("mgmt_ip")} placeholder="192.168.1.1" />
            </Field>
            <Field label="Prefix">
              <Input value={f.mgmt_prefix || 24} onChange={set("mgmt_prefix")} type="number" min="8" max="30" />
            </Field>
            <Field label="DHCP Start">
              <Input value={f.mgmt_dhcp_start || "192.168.1.100"} onChange={set("mgmt_dhcp_start")} placeholder="192.168.1.100" />
            </Field>
            <Field label="DHCP End">
              <Input value={f.mgmt_dhcp_end || "192.168.1.150"} onChange={set("mgmt_dhcp_end")} placeholder="192.168.1.150" />
            </Field>
            <Field label="DHCP Lease">
              <Select value={f.mgmt_dhcp_lease || "12h"} onChange={set("mgmt_dhcp_lease")} options={[
                { value: "1h", label: "1 hour" }, { value: "6h", label: "6 hours" },
                { value: "12h", label: "12 hours" }, { value: "24h", label: "24 hours" },
              ]} />
            </Field>
          </div>
          <div className={sharedStyles.toggleRow}>
            <Toggle value={!!f.mgmt_icmp_echo} onChange={set("mgmt_icmp_echo")} label="Allow ping (ICMP echo)" />
          </div>
          {f.mgmt_enabled && f.mgmt_interface === f.wan_interface && (
            <div className={styles.mgmtWarnMsg}>
              ⚠ Management interface is the same as WAN — this exposes the admin UI to the internet.
            </div>
          )}
        </div>

        <div className={`${sharedStyles.formActions} ${styles.mt16}`}>
          <Btn onClick={save}>{saved ? "✓ Saved" : "Save Management Config"}</Btn>
        </div>
      </Card>
    </>
  );
}

// ── DNS tab ───────────────────────────────────────────────────────────────────
const defDns = { hostname: "", ip: "", description: "" };
