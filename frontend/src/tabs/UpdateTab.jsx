// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState, useEffect, useRef } from "react";
import { GET, POST } from "../api.js";
import { Btn, Card, CodeBlock } from "../components/index.js";
import styles from "./UpdateTab.module.css";

// Progress is polled rather than streamed (SSE) because the update restarts
// the backend partway through — polling survives that restart window, an
// SSE connection would just die.
const POLL_INTERVAL_MS   = 1500;
const OVERALL_TIMEOUT_MS = 3 * 60 * 1000;
const TERMINAL_STATES    = ["success", "rolledback", "failed"];

export function UpdateTab() {
  const [status,  setStatus]  = useState(null);   // null | "checking" | "ready" | "error"
  const [info,    setInfo]    = useState(null);   // check response
  const [applyErr, setApplyErr] = useState("");

  const [updateState, setUpdateState] = useState(null); // polled /api/update/status
  const [polling,     setPolling]     = useState(false);
  const [restarting,  setRestarting]  = useState(false); // polls failing — likely mid-restart
  const [timedOut,    setTimedOut]    = useState(false);

  const pollTimer   = useRef(null);
  const pollStarted = useRef(0);
  const logRef      = useRef(null);

  useEffect(() => {
    checkForUpdate();
    resumeIfInFlight();
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll log to bottom as lines arrive
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [updateState?.log]);

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

  async function resumeIfInFlight() {
    // If the page was reloaded mid-update, pick the progress display back up.
    try {
      const s = await GET("/api/update/status");
      setUpdateState(s);
      if (s.state === "running") startPolling();
    } catch {
      // nothing to resume
    }
  }

  function stopPolling() {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
    setPolling(false);
  }

  function startPolling() {
    setPolling(true);
    setTimedOut(false);
    setRestarting(false);
    pollStarted.current = Date.now();
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = setInterval(pollOnce, POLL_INTERVAL_MS);
    pollOnce();
  }

  async function pollOnce() {
    try {
      const s = await GET("/api/update/status");
      setRestarting(false);
      setUpdateState(s);
      if (TERMINAL_STATES.includes(s.state)) {
        stopPolling();
        checkForUpdate(); // refresh the "installed" version display
        return;
      }
    } catch {
      // The backend is very likely mid-restart — don't flip to an error
      // state, just note it and keep polling quietly.
      setRestarting(true);
    }
    if (Date.now() - pollStarted.current > OVERALL_TIMEOUT_MS) {
      setTimedOut(true);
      stopPolling();
    }
  }

  async function applyUpdate() {
    setApplyErr("");
    try {
      await POST("/api/update/apply", {});
      startPolling();
    } catch (e) {
      setApplyErr(e.message);
    }
  }

  const upToDate  = info?.up_to_date === true;
  const hasUpdate = info?.up_to_date === false;

  const s            = updateState;
  const isSuccess    = s?.state === "success"    && !polling;
  const isRolledBack = s?.state === "rolledback" && !polling;
  const isFailed     = s?.state === "failed"     && !polling;
  const showProgress = polling || isSuccess || isRolledBack || isFailed || timedOut;

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
                <div key={i}>{line || " "}</div>
              ))}
            </div>
          </div>
        )}

        {applyErr && <div className={styles.errorMsg}>⚠ {applyErr}</div>}

        <div className={styles.actions}>
          <Btn variant="ghost" small onClick={checkForUpdate} disabled={status === "checking" || polling}>
            ↻ Check again
          </Btn>
          {hasUpdate && !polling && (
            <Btn onClick={applyUpdate}>
              ⬆ Install {info.latest}
            </Btn>
          )}
          {polling && (
            <Btn disabled>Applying…</Btn>
          )}
        </div>
      </Card>

      {/* Update progress — shown once apply starts (or resumed after reload) */}
      {showProgress && (
        <Card title="Update Progress">
          {s?.phase && (
            <div className={styles.progressRow}>
              <span className={styles.phaseLabel}>{s.phase}</span>
              <div className={styles.progressBar}>
                <div className={styles.progressBarFill} style={{ width: `${s.percent ?? 0}%` }} />
              </div>
              <span className={styles.phaseLabel}>{s.percent ?? 0}%</span>
            </div>
          )}

          <div className={styles.logWrapper} ref={logRef}>
            {(s?.log ?? []).map((line, i) => {
              const isError   = line.startsWith("ERROR") || line.includes("ERROR:");
              const isSuccessLine = line.startsWith("✓") || line.includes("✓");
              const isWarn    = line.includes("⚠") || line.startsWith("WARNING");
              return (
                <div
                  key={i}
                  className={
                    isError       ? styles.logError   :
                    isSuccessLine ? styles.logSuccess  :
                    isWarn        ? styles.logWarn     :
                    styles.logLine
                  }
                >
                  {line || " "}
                </div>
              );
            })}
            {restarting && (
              <div className={styles.logWarn}>
                … applying (service restarting — this is expected) …
              </div>
            )}
          </div>

          {isSuccess && (
            <div className={styles.restartNote}>
              ✓ Update complete — now running v{s.installed_version || s.to_version}, confirmed healthy.
              <div className={styles.mt8}>
                <Btn small onClick={() => window.location.reload()}>Reload page</Btn>
              </div>
            </div>
          )}

          {isSuccess && s.config_pending && (
            <div className={styles.rollbackNote}>
              ⚠ This release changed generated configuration (firewall/DNS/daemons) that hasn't
              been activated yet — click <strong>⚡ Apply</strong> above to push it live.
            </div>
          )}

          {isRolledBack && (
            <div className={styles.rollbackNote}>
              ⚠ {s.message || `Update failed and was rolled back to v${s.from_version}.`}
              <div className={styles.failNote}>
                No action needed — the device is running the previous version. If you want to
                investigate, update manually over SSH:
                <CodeBlock content={"sudo python3 /opt/spud-router/update.py"} />
              </div>
            </div>
          )}

          {isFailed && (
            <div className={styles.failNote}>
              ⚠ {s.message || "Update failed."}
              <div className={styles.mt8}>
                Update manually over SSH:
                <CodeBlock content={"sudo python3 /opt/spud-router/update.py"} />
              </div>
            </div>
          )}

          {timedOut && !isSuccess && !isRolledBack && !isFailed && (
            <div className={styles.failNote}>
              This is taking longer than expected. The device may still be mid-update — check
              back in a minute, or check manually over SSH:
              <CodeBlock content={"cat /run/spud-router/update-status.json"} />
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
          "curl -L https://github.com/bensonbrett/spud_router/releases/download/v1.1.0/spud-router-v1.1.0.tar.gz | tar xz\nsudo bash install.sh"
        } />
      </Card>
    </>
  );
}
