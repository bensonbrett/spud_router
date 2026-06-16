import { useState, useEffect } from "react";
import { GET } from "../api.js";
import { Btn, Card, CodeBlock } from "../components/index.js";
import styles from "./PreviewTab.module.css";
import sharedStyles from "./shared.module.css";

export function PreviewTab() {
  const [preview, setPreview] = useState(null);
  const [tab, setTab] = useState("netplan");
  useEffect(() => { GET("/api/preview").then(setPreview).catch(() => {}); }, []);

  return (
    <>
      <div className={sharedStyles.refreshRow}>
        <Btn variant="ghost" small onClick={() => GET("/api/preview").then(setPreview)}>↻ Regenerate</Btn>
      </div>
      {preview && (
        <Card title="Generated Config Files">
          <div className={styles.previewTabs}>
            {["netplan", "dnsmasq", "iptables"].map((t) => (
              <button key={t} className={styles.previewTab} data-active={tab === t} onClick={() => setTab(t)}>{t}</button>
            ))}
          </div>
          <CodeBlock content={preview[tab]} />
        </Card>
      )}
    </>
  );
}

// ── Settings tab ──────────────────────────────────────────────────────────────