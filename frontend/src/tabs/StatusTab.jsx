import { useState, useEffect } from "react";
import { GET } from "../api.js";
import { Btn, Card, CodeBlock } from "../components/index.js";
import styles from "./StatusTab.module.css";
import sharedStyles from "./shared.module.css";

export function StatusTab() {
  const [status, setStatus] = useState(null);
  const load = () => GET("/api/status").then(setStatus).catch(() => {});
  useEffect(() => { load(); }, []);

  return (
    <>
      <div className={sharedStyles.refreshRow}>
        <Btn variant="ghost" small onClick={load}>↻ Refresh</Btn>
      </div>
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