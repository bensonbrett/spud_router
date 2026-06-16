import { useState, useEffect, useRef } from "react";
import { GET, POST } from "../api.js";
import { Btn, Card, CodeBlock } from "../components/index.js";
import styles from "./UpdateTab.module.css";

export function UpdateTab() {
  const [status,   setStatus]   = useState(null);   // null | "checking" | "ready" | "error"
  const [info,     setInfo]     = useState(null);   // check response
  const [applying, setApplying] = useState(false);
  const [log,      setLog]      = useState([]);
  const [exitCode, setExitCode] = useState(null);
  const logRef = useRef(null);

  useEffect(() => { checkForUpdate(); }, []);

  // Auto-scroll log to bottom as lines arrive
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [log]);

  async function checkForUpdate() {
    setStatus("checking");
    setInfo(null);
    try {
      const data = await GET("/api/update/check");
      setInfo(data);
      setStatus(data.error ? "error" : "ready");
    } catch (e) {
      setInfo({ error: e.message });
      setStatus("error");
    }
  }

  async function applyUpdate() {
    setApplying(true);
    setLog([]);
    setExitCode(null);

    const token = sessionStorage.getItem("spud_token") || "";

    try {
      const resp = await fetch("/api/update/apply", {
        method: "POST",
        headers: { "X-Session-Token": token },
      });

      const reader  = resp.body.getReader();
      const decoder = new TextDecoder();
      let   buffer  = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop(); // keep incomplete event

        for (const event of events) {
          const line = event.replace(/^data: /, "").trim();
          if (!line) continue;
          try {
            const msg = JSON.parse(line);
            if (msg.line !== undefined) {
              setLog((prev) => [...prev, msg.line]);
            }
            if (msg.done) {
              setExitCode(msg.exit_code ?? 0);
            }
          } catch {
            // malformed event — ignore
          }
        }
      }
    } catch (e) {
      setLog((prev) => [...prev, `ERROR: ${e.message}`]);
      setExitCode(1);
    } finally {
      setApplying(false);
    }
  }

  const upToDate  = info?.up_to_date === true;
  const hasUpdate = info?.up_to_date === false;
  const succeeded = exitCode === 0;
  const alreadyOk = exitCode === 2;

  return (
    <>
      {/* Version status card */}
      <Card title="Software Update">
        <div className={styles.versionRow}>
          <div className={styles.versionItem}>
            <span className={styles.versionLabel}>Installed</span>
            <span className={styles.versionValue}>{info?.current ?? "—"}</span>
          </div>
          <div className={styles.versionArrow}>→</div>
          <div className={styles.versionItem}>
            <span className={styles.versionLabel}>Latest</span>
            <span className={styles.versionValue}>
              {status === "checking" ? (
                <span className={styles.checking}>Checking…</span>
              ) : info?.latest ? (
                info.latest
              ) : (
                "—"
              )}
            </span>
          </div>
          {upToDate && (
            <span className={styles.pillUpToDate}>✓ Up to date</span>
          )}
          {hasUpdate && (
            <span className={styles.pillUpdateAvailable}>Update available</span>
          )}
        </div>

        {info?.error && (
          <div className={styles.errorMsg}>
            ⚠ {info.error}
          </div>
        )}

        {hasUpdate && info?.changelog && (
          <div className={styles.changelog}>
            <div className={styles.changelogTitle}>
              Release notes for {info.tag}
            </div>
            <div className={styles.changelogBody}>
              {info.changelog.split("\n").slice(0, 30).map((line, i) => (
                <div key={i}>{line || "\u00a0"}</div>
              ))}
            </div>
          </div>
        )}

        <div className={styles.actions}>
          <Btn variant="ghost" small onClick={checkForUpdate} disabled={status === "checking" || applying}>
            ↻ Check again
          </Btn>
          {hasUpdate && !applying && exitCode === null && (
            <Btn onClick={applyUpdate} disabled={applying}>
              ⬆ Install {info.latest}
            </Btn>
          )}
          {applying && (
            <Btn disabled>Installing…</Btn>
          )}
        </div>
      </Card>

      {/* Update log — shown once apply starts */}
      {log.length > 0 && (
        <Card title="Update Log">
          <div className={styles.logWrapper} ref={logRef}>
            {log.map((line, i) => {
              const isError   = line.startsWith("ERROR") || line.startsWith("  ERROR");
              const isSuccess = line.startsWith("✓") || line.includes("✓");
              const isWarn    = line.startsWith("  ⚠") || line.startsWith("WARNING");
              return (
                <div
                  key={i}
                  className={
                    isError   ? styles.logError   :
                    isSuccess ? styles.logSuccess  :
                    isWarn    ? styles.logWarn     :
                    styles.logLine
                  }
                >
                  {line || "\u00a0"}
                </div>
              );
            })}
            {exitCode !== null && (
              <div className={succeeded || alreadyOk ? styles.logSuccess : styles.logError}>
                {succeeded  ? "── Update complete ──" :
                 alreadyOk  ? "── Already up to date ──" :
                              "── Update failed ──"}
              </div>
            )}
          </div>

          {exitCode !== null && succeeded && (
            <div className={styles.restartNote}>
              The service has been restarted. Reload this page to use the new version.
            </div>
          )}

          {exitCode !== null && !succeeded && !alreadyOk && (
            <div className={styles.failNote}>
              Update failed. You can update manually over SSH:
              <CodeBlock content={"sudo python3 /opt/spud-router/update.py"} />
            </div>
          )}
        </Card>
      )}

      {/* Manual update instructions */}
      <Card title="Manual Update">
        <p className={styles.manualDesc}>
          To update manually over SSH from the device:
        </p>
        <CodeBlock content={"sudo python3 /opt/spud-router/update.py"} />
        <p className={styles.manualDesc} style={{ marginTop: 12 }}>
          Or to install a specific version:
        </p>
        <CodeBlock content={
          "curl -L https://github.com/yourusername/spud-router/releases/download/v1.1.0/spud-router-v1.1.0.tar.gz | tar xz\nsudo bash install.sh"
        } />
      </Card>
    </>
  );
}
