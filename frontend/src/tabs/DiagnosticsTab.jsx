import { useState, useEffect } from "react";
import { GET } from "../api.js";
import { Btn, Card } from "../components/index.js";
import styles from "./DiagnosticsTab.module.css";
import sharedStyles from "./shared.module.css";

function StatusDot({ carrier, operstate }) {
  const up = carrier === true && operstate === "up";
  const carrier_only = carrier === true && operstate !== "up";
  const cls = up ? styles.dotUp : carrier_only ? styles.dotPartial : styles.dotDown;
  const label = carrier === null ? "no interface" : up ? "up" : carrier_only ? `carrier, ${operstate}` : "down";
  return <span className={`${styles.dot} ${cls}`} title={label} />;
}

function IfaceCard({ iface }) {
  const isUp = iface.carrier === true && iface.operstate === "up";
  return (
    <Card title={
      <span className={styles.cardTitle}>
        <StatusDot carrier={iface.carrier} operstate={iface.operstate} />
        <span className={styles.ifaceName}>{iface.name}</span>
        {iface.vlan_name && <span className={styles.vlanBadge}>VLAN {iface.vlan_id} · {iface.vlan_name}</span>}
        {iface.role === "wan" && <span className={styles.roleBadge}>WAN</span>}
        {iface.is_default_gw && <span className={styles.gwBadge}>default GW</span>}
      </span>
    }>
      <div className={styles.ifaceBody}>
        <div className={styles.row}>
          <span className={styles.label}>State</span>
          <span className={isUp ? styles.valOk : styles.valWarn}>
            {iface.carrier === null ? "interface not found" : `${iface.operstate} (carrier: ${iface.carrier ? "yes" : "no"})`}
          </span>
        </div>
        {iface.cfg_address && (
          <div className={styles.row}>
            <span className={styles.label}>Configured IP</span>
            <span className={iface.ip_present ? styles.valOk : styles.valWarn}>
              {iface.cfg_address}
              {iface.ip_present ? " ✓" : " — not assigned"}
            </span>
          </div>
        )}
        {iface.addresses?.length > 0 && (
          <div className={styles.row}>
            <span className={styles.label}>Addresses</span>
            <span className={styles.mono}>{iface.addresses.join(", ")}</span>
          </div>
        )}
        {iface.role === "vlan" && (
          <div className={styles.row}>
            <span className={styles.label}>DHCP leases</span>
            <span className={styles.mono}>
              {iface.leases?.length > 0
                ? iface.leases.map(l => `${l.ip} (${l.hostname})`).join(", ")
                : "none"}
            </span>
          </div>
        )}
        {iface.hint && (
          <div className={styles.hint}>⚠ {iface.hint}</div>
        )}
      </div>
    </Card>
  );
}

export function DiagnosticsTab() {
  const [data, setData] = useState(null);
  const load = () => GET("/api/diagnostics").then(setData).catch(() => {});
  useEffect(() => { load(); }, []);

  return (
    <>
      <div className={sharedStyles.refreshRow}>
        <Btn variant="ghost" small onClick={load}>↻ Refresh</Btn>
      </div>
      {data && (
        <>
          {data.default_route && (
            <div className={styles.routeBanner}>
              <span className={styles.routeLabel}>Default route</span>
              <span className={styles.routeVal}>{data.default_route}</span>
            </div>
          )}
          <div className={styles.grid}>
            {data.wan && <IfaceCard iface={data.wan} />}
            {data.vlans?.map(v => <IfaceCard key={v.name} iface={v} />)}
          </div>
          {!data.wan && data.vlans?.length === 0 && (
            <p className={sharedStyles.emptyState}>No interfaces configured. Add a WAN or VLAN first.</p>
          )}
        </>
      )}
    </>
  );
}
