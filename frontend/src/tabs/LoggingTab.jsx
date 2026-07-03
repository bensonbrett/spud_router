import { useState, useEffect } from "react";
import { PUT, POST } from "../api.js";
import { Btn, Card, ErrMsg, Field, Input, OkMsg, Select, Toggle } from "../components/index.js";
import sharedStyles from "./shared.module.css";

const PROTOCOL_OPTS = [
  { value: "udp", label: "UDP" },
  { value: "tcp", label: "TCP" },
  { value: "tls", label: "TLS (encrypted)" },
];

const FACILITY_OPTS = [
  "*", "kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news",
  "uucp", "cron", "authpriv", "ftp",
  "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7",
].map((v) => ({ value: v, label: v }));

const SEVERITY_OPTS = [
  "*", "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug",
].map((v) => ({ value: v, label: v }));

const defForm = { enabled: false, server: "", port: 514, protocol: "udp", facility: "*", severity: "*", keep_local: true };

export function LoggingTab({ state, onReload, showToast }) {
  const [f, setF] = useState(defForm);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  useEffect(() => {
    if (state?.syslog) setF({ ...defForm, ...state.syslog });
  }, [state]);

  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }));

  const save = async () => {
    setBusy(true); setErr("");
    try {
      await PUT("/api/syslog", { ...f, port: parseInt(f.port) });
      onReload();
      showToast("Syslog settings saved");
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const testConnection = async () => {
    setTesting(true); setTestResult(null); setErr("");
    try {
      const res = await POST("/api/syslog/test", { ...f, port: parseInt(f.port) });
      setTestResult(res);
    } catch (e) {
      setErr(e.message);
    } finally {
      setTesting(false);
    }
  };

  return (
    <Card title="Remote Syslog Forwarding">
      <p className={sharedStyles.emptyState}>
        Forward system logs to a remote syslog server. Local logging continues unless
        "keep local" is disabled below.
      </p>
      <div className={sharedStyles.toggleRow}>
        <Toggle value={f.enabled} onChange={set("enabled")} label="Enable forwarding" />
        <Toggle value={f.keep_local} onChange={set("keep_local")} label="Keep local logs" />
      </div>
      <div className={sharedStyles.formGrid3}>
        <Field label="Server"><Input value={f.server} onChange={set("server")} placeholder="logs.example.com" disabled={!f.enabled} /></Field>
        <Field label="Port"><Input value={f.port} onChange={set("port")} type="number" min="1" max="65535" disabled={!f.enabled} /></Field>
        <Field label="Protocol"><Select value={f.protocol} onChange={set("protocol")} options={PROTOCOL_OPTS} /></Field>
        <Field label="Facility"><Select value={f.facility} onChange={set("facility")} options={FACILITY_OPTS} /></Field>
        <Field label="Severity"><Select value={f.severity} onChange={set("severity")} options={SEVERITY_OPTS} /></Field>
      </div>
      <ErrMsg msg={err} />
      <div className={sharedStyles.formActions}>
        <Btn onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</Btn>
        <Btn variant="ghost" onClick={testConnection} disabled={testing || !f.server}>
          {testing ? "Testing…" : "Test connection"}
        </Btn>
      </div>
      {testResult && (testResult.reachable
        ? <OkMsg msg={testResult.message} />
        : <ErrMsg msg={testResult.message} />
      )}
    </Card>
  );
}
