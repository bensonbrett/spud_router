// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState, useEffect } from "react";
import { GET } from "../api.js";
import { Btn, Card, CodeBlock } from "../components/index.js";
import styles from "./StatusTab.module.css";
import sharedStyles from "./shared.module.css";

// Poll /api/system/monitor while this tab is mounted — cheap, /proc-only
// reads on the backend, but the interval must stop on unmount or it leaks
// across tab switches.
const MONITOR_POLL_MS = 2000;

const DISK_LABELS = { root: "/", spud_conf: "/etc/spud-router" };

function formatBytes(bytes) {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let val = bytes;
  let i = 0;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i += 1;
  }
  return `${val.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatKb(kb) {
  return kb == null ? "—" : formatBytes(kb * 1024);
}

function Gauge({ label, percent, detail }) {
  const pct = percent == null ? null : Math.max(0, Math.min(100, percent));
  return (
    <div className={styles.gauge}>
      <div className={styles.gaugeHeader}>
        <span className={styles.gaugeLabel}>{label}</span>
        <span className={styles.gaugeValue}>{pct == null ? "—" : `${pct.toFixed(0)}%`}</span>
      </div>
      <div className={styles.gaugeBar}>
        <div className={styles.gaugeBarFill} style={{ width: `${pct ?? 0}%` }} />
      </div>
      <div className={styles.gaugeDetail}>{detail}</div>
    </div>
  );
}

export function StatusTab() {
  const [status, setStatus] = useState(null);
  const [monitor, setMonitor] = useState(null);
  const load = () => GET("/api/status").then(setStatus).catch(() => {});
  useEffect(() => { load(); }, []);

  useEffect(() => {
    const poll = () => GET("/api/system/monitor").then(setMonitor).catch(() => {});
    poll();
    const id = setInterval(poll, MONITOR_POLL_MS);
    return () => clearInterval(id);
  }, []);

  const mem       = monitor?.memory;
  const memPct    = mem ? (mem.mem_used_kb / mem.mem_total_kb) * 100 : null;
  const disks     = monitor?.disks || {};
  const ifaces    = monitor?.interfaces || {};
  const ifaceKeys = Object.keys(ifaces);

  return (
    <>
      <div className={sharedStyles.refreshRow}>
        <Btn variant="ghost" small onClick={load}>↻ Refresh</Btn>
      </div>

      {monitor && (
        <Card title="System Monitor">
          <div className={styles.monitorGrid}>
            <Gauge
              label="Memory"
              percent={memPct}
              detail={mem ? `${formatKb(mem.mem_used_kb)} / ${formatKb(mem.mem_total_kb)}` : "unavailable"}
            />
            <Gauge
              label="CPU"
              percent={monitor.cpu_percent}
              detail={monitor.cpu_percent == null ? "unavailable" : "aggregate, all cores"}
            />
            {Object.entries(disks).map(([key, d]) => (
              <Gauge
                key={key}
                label={`Disk ${DISK_LABELS[key] || key}`}
                percent={(d.used_bytes / d.total_bytes) * 100}
                detail={`${formatBytes(d.used_bytes)} / ${formatBytes(d.total_bytes)}`}
              />
            ))}
          </div>
          <div className={styles.loadRow}>
            <span className={sharedStyles.cardHeaderMeta}>Load average</span>
            <span className={styles.loadValue}>
              {monitor.load
                ? `${monitor.load.load1.toFixed(2)} / ${monitor.load.load5.toFixed(2)} / ${monitor.load.load15.toFixed(2)}  (1 / 5 / 15 min)`
                : "unavailable"}
            </span>
          </div>
        </Card>
      )}

      {ifaceKeys.length > 0 && (
        <Card title="Interface Counters">
          <div className={styles.ifaceHeaderRow}>
            <span className={styles.ifaceName}>Interface</span>
            <span className={styles.ifaceCol}>RX bytes</span>
            <span className={styles.ifaceCol}>RX pkts</span>
            <span className={styles.ifaceCol}>RX err/drop</span>
            <span className={styles.ifaceCol}>TX bytes</span>
            <span className={styles.ifaceCol}>TX pkts</span>
            <span className={styles.ifaceCol}>TX err/drop</span>
          </div>
          {ifaceKeys.map((name) => {
            const c = ifaces[name];
            return (
              <div key={name} className={styles.ifaceRow}>
                <span className={styles.ifaceName}>{name}</span>
                <span className={styles.ifaceCol}>{formatBytes(c.rx_bytes)}</span>
                <span className={styles.ifaceCol}>{c.rx_packets}</span>
                <span className={styles.ifaceCol}>{c.rx_errs} / {c.rx_drop}</span>
                <span className={styles.ifaceCol}>{formatBytes(c.tx_bytes)}</span>
                <span className={styles.ifaceCol}>{c.tx_packets}</span>
                <span className={styles.ifaceCol}>{c.tx_errs} / {c.tx_drop}</span>
              </div>
            );
          })}
        </Card>
      )}

      {status && (
        <>
          <div className={styles.statusGrid}>
            <Card title="Interfaces"><CodeBlock content={status.interfaces || ""} /></Card>
            <Card title="Routing Table"><CodeBlock content={status.routes || ""} /></Card>
          </div>
          <Card title={`DHCP Leases (${status.leases?.length || 0})`}>
            {status.leases?.length === 0 && <p className={sharedStyles.emptyState}>No active leases.</p>}
            {status.leases?.map((l) => (
              <div key={l.ip} className={styles.statusLine}>
                <span className={styles.statusIp}>{l.ip}</span>
                <span className={styles.statusMac}>{l.mac}</span>
                <span className={styles.statusHostname}>{l.hostname}</span>
              </div>
            ))}
          </Card>
        </>
      )}
    </>
  );
}