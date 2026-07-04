// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState, useEffect } from "react";
import { PUT } from "../api.js";
import { Btn, Card, ErrMsg, Field, Input, Select, Toggle } from "../components/index.js";
import sharedStyles from "./shared.module.css";

const defForm = {
  enabled: false, version: "v2c", community_ro: "", community_rw: "",
  allowlist: [], bind_interface: "", location: "", contact: "",
};

export function SnmpTab({ state, interfaces, onReload, showToast }) {
  const [f, setF] = useState(defForm);
  const [allowlistInput, setAllowlistInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (state?.snmp) setF({ ...defForm, ...state.snmp });
  }, [state]);

  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));

  const addAllowlistEntry = () => {
    if (allowlistInput && !f.allowlist.includes(allowlistInput)) {
      set("allowlist")([...f.allowlist, allowlistInput]);
      setAllowlistInput("");
    }
  };

  const save = async () => {
    setBusy(true); setErr("");
    try {
      await PUT("/api/snmp", f);
      onReload();
      showToast("SNMP settings saved");
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const ifOpts = [{ value: "", label: "All interfaces" }, ...(interfaces || []).map((i) => ({ value: i.name, label: i.name }))];

  return (
    <Card title="SNMP Agent (Net-SNMP, v2c)">
      <p className={sharedStyles.emptyState}>
        Exposes MIB-II for network monitoring tools. Community strings are write-only —
        the field shows a placeholder once set and is never read back.
      </p>
      <div className={sharedStyles.toggleRow}>
        <Toggle value={f.enabled} onChange={set("enabled")} label="Enable SNMP agent" />
      </div>
      <div className={sharedStyles.formGrid3}>
        <Field label="Read-only community" help="Required when enabled">
          <Input value={f.community_ro} onChange={set("community_ro")} type="password" placeholder="public" disabled={!f.enabled} />
        </Field>
        <Field label="Read-write community" help="Optional — leave blank for read-only">
          <Input value={f.community_rw} onChange={set("community_rw")} type="password" placeholder="" disabled={!f.enabled} />
        </Field>
        <Field label="Bind interface" help="Blank = all interfaces">
          <Select value={f.bind_interface} onChange={set("bind_interface")} options={ifOpts} />
        </Field>
        <Field label="Location (sysLocation)"><Input value={f.location} onChange={set("location")} placeholder="Server Room" /></Field>
        <Field label="Contact (sysContact)"><Input value={f.contact} onChange={set("contact")} placeholder="admin@example.com" /></Field>
      </div>
      <Field label="Allowlist" help="Source IPs/CIDRs allowed to poll. Empty = accept from any source (not recommended).">
        <div className={sharedStyles.toggleRow}>
          <Input
            value={allowlistInput}
            onChange={setAllowlistInput}
            placeholder="192.168.10.0/24"
            onKeyDown={(e) => e.key === "Enter" && addAllowlistEntry()}
          />
          <Btn small onClick={addAllowlistEntry}>+ Add</Btn>
        </div>
        <div className={sharedStyles.toggleRow}>
          {f.allowlist.map((entry) => (
            <span key={entry}>
              {entry}
              <button onClick={() => set("allowlist")(f.allowlist.filter((x) => x !== entry))}>×</button>
            </span>
          ))}
          {f.allowlist.length === 0 && <span className={sharedStyles.emptyState}>No allowlist entries — accepting from any source</span>}
        </div>
      </Field>
      <ErrMsg msg={err} />
      <div className={sharedStyles.formActions}>
        <Btn onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</Btn>
      </div>
    </Card>
  );
}
