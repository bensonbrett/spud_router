import { useState, useCallback, useEffect } from "react";
import { GET, POST } from "./api.js";
import { Btn, ErrorBoundary } from "./components/index.js";
import { LoginScreen }  from "./tabs/LoginScreen.jsx";
import { VlansTab }     from "./tabs/VlansTab.jsx";
import { WanTab }       from "./tabs/WanTab.jsx";
import { DnsTab }       from "./tabs/DnsTab.jsx";
import { RoutesTab }    from "./tabs/RoutesTab.jsx";
import { FirewallTab }  from "./tabs/FirewallTab.jsx";
import { TailscaleTab } from "./tabs/TailscaleTab.jsx";
import { StatusTab }    from "./tabs/StatusTab.jsx";
import { PreviewTab }   from "./tabs/PreviewTab.jsx";
import { SettingsTab }  from "./tabs/SettingsTab.jsx";
import { UpdateTab }    from "./tabs/UpdateTab.jsx";
import { WirelessTab }     from "./tabs/WirelessTab.jsx";
import { DiagnosticsTab } from "./tabs/DiagnosticsTab.jsx";
import { LoggingTab } from "./tabs/LoggingTab.jsx";
import { SnmpTab } from "./tabs/SnmpTab.jsx";
import styles from "./App.module.css";

const TABS = [
  { id: "vlans",     label: "VLANs",     icon: "⫿" },
  { id: "wan",       label: "WAN",       icon: "🌐" },
  { id: "dns",       label: "DNS",       icon: "◈"  },
  { id: "routes",    label: "Routes",    icon: "↗"  },
  { id: "firewall",  label: "Firewall",  icon: "🛡" },
  { id: "tailscale", label: "Tailscale", icon: "🔒" },
  { id: "wireless",     label: "Wireless",    icon: "📶" },
  { id: "diagnostics",  label: "Diagnostics", icon: "⊡"  },
  { id: "logging",      label: "Logging",     icon: "▤"  },
  { id: "snmp",         label: "SNMP",        icon: "📡" },
  { id: "status",       label: "Status",      icon: "◉"  },
  { id: "preview",   label: "Preview",   icon: "⟨⟩" },
  { id: "update",    label: "Update",    icon: "⬆"  },
  { id: "settings",  label: "Settings",  icon: "⚙"  },
];

export default function App() {
  // null = still checking, true = authenticated, false = needs login
  const [authed,     setAuthed]     = useState(null);
  const [tab,        setTab]        = useState("vlans");
  const [state,      setState]      = useState(null);
  const [interfaces, setInterfaces] = useState([]);
  const [applying,   setApplying]   = useState(false);
  const [applySteps, setApplySteps] = useState([]);
  const [toast,      setToast]      = useState("");
  const [rebootNeeded, setRebootNeeded] = useState(false);
  const [pendingChanges, setPendingChanges] = useState(false);
  const [confirmingReboot, setConfirmingReboot] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  // { token, deadline } while an apply is armed (pending confirmation before
  // the connectivity-watchdog auto-reverts it); null otherwise.
  const [armed, setArmed] = useState(null);
  const [armedRemaining, setArmedRemaining] = useState(0);

  const refreshApplyStatus = useCallback(() => {
    GET("/api/apply/status").then(s => setPendingChanges(s.pending)).catch(() => {});
  }, []);

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2800);
  };

  const handleReboot = async () => {
    try {
      await POST("/api/system/reboot");
      setRebooting(true);
      setConfirmingReboot(false);
      showToast("Rebooting… the device will be back in ~1–2 minutes.");
    } catch (e) {
      showToast("Reboot failed: " + e.message);
    }
  };

  const reload = useCallback(async () => {
    const [s, ifaces] = await Promise.all([GET("/api/state"), GET("/api/interfaces")]);
    setState(s);
    setInterfaces(ifaces);
  }, []);

  // On mount, probe the API to see if the session cookie is still valid.
  // This is the only auth-state check — no sessionStorage involved.
  useEffect(() => {
    GET("/api/auth/status")
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    if (authed === true) {
      GET("/api/interfaces").then(setInterfaces).catch(() => {});
      GET("/api/system/status").then(s => setRebootNeeded(s.reboot_needed)).catch(() => {});
      refreshApplyStatus();
      // Restore the countdown banner if an apply is still armed from before
      // a page reload or reconnect (e.g. the network change that triggered
      // this reconnect is itself the one still awaiting confirmation).
      GET("/api/apply/armed").then((a) => {
        if (a.armed) setArmed({ token: a.token, deadline: Date.now() + a.remaining_seconds * 1000 });
      }).catch(() => {});
    }
  }, [authed, refreshApplyStatus]);

  // Re-check on every tab switch too, so the banner reflects edits made
  // in whichever tab the admin was just on.
  useEffect(() => {
    if (authed === true) refreshApplyStatus();
  }, [tab, authed, refreshApplyStatus]);

  // Countdown tick for the armed-apply banner — computed from a fixed
  // deadline timestamp (not a decrementing counter) so it can't drift.
  useEffect(() => {
    if (!armed) return;
    const tick = () => {
      const left = Math.max(0, Math.round((armed.deadline - Date.now()) / 1000));
      setArmedRemaining(left);
      if (left <= 0) setArmed(null);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [armed]);

  const handleApply = async () => {
    setApplying(true);
    setApplySteps([]);
    try {
      const res = await POST("/api/apply", { dry_run: false });
      setApplySteps(res.steps || []);
      showToast("Config applied!");
      refreshApplyStatus();
      if (res.armed) {
        setArmed({ token: res.token, deadline: Date.now() + res.window_seconds * 1000 });
      } else {
        setArmed(null);
      }
    } catch (e) {
      showToast("Apply failed: " + e.message);
    } finally {
      setApplying(false);
    }
  };

  const handleConfirmApply = async () => {
    if (!armed) return;
    try {
      await POST("/api/apply/confirm", { token: armed.token });
      setArmed(null);
      showToast("Changes kept.");
    } catch (e) {
      showToast("Could not confirm: " + e.message);
    }
  };

  const handleLogout = async () => {
    await POST("/api/auth/logout").catch(() => {});
    setAuthed(false);
    setState(null);
  };

  if (authed === null) return null;  // brief loading flash before cookie check resolves

  if (!authed) {
    return <LoginScreen onLogin={() => { setAuthed(true); reload(); }} />;
  }

  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <span className={styles.headerLogo}>🥔</span>
        <span className={styles.headerTitle}>spud<span>-router</span></span>
        <nav className={styles.headerNav}>
          {TABS.map((t) => (
            <button
              key={t.id}
              className={styles.navTab}
              data-active={tab === t.id}
              onClick={() => setTab(t.id)}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </nav>
        <Btn onClick={handleApply} disabled={applying} small>
          ⚡ {applying ? "Applying…" : "Apply"}
        </Btn>
      </header>

      {applySteps.length > 0 && (
        <div className={styles.applyStrip}>
          {applySteps.map((s, i) => (
            <span key={i} className={styles.applyStripItem}>✓ {s}</span>
          ))}
          <button className={styles.applyStripClose} onClick={() => setApplySteps([])}>×</button>
        </div>
      )}

      {armed && (
        <div className={styles.armedBanner}>
          <span>
            ⏱ <strong>Confirm this change</strong> — if not confirmed within {armedRemaining}s, the
            device automatically reverts to the previous configuration (in case this change broke
            connectivity to it).
          </span>
          <Btn small variant="danger" onClick={handleConfirmApply}>✓ Keep changes</Btn>
        </div>
      )}

      {rebootNeeded && (
        <div className={styles.rebootBanner}>
          <span>
            ⚠️ <strong>Reboot required</strong> — Network changes won't take effect until the device reboots.
          </span>
          {rebooting ? (
            <span className={styles.rebootBannerStatus}>Rebooting… back in ~1–2 min</span>
          ) : confirmingReboot ? (
            <span className={styles.rebootBannerActions}>
              <Btn small variant="danger" onClick={handleReboot}>Confirm reboot</Btn>
              <Btn small variant="ghost" onClick={() => setConfirmingReboot(false)}>Cancel</Btn>
            </span>
          ) : (
            <Btn small onClick={() => setConfirmingReboot(true)}>⏻ Reboot now</Btn>
          )}
        </div>
      )}

      {pendingChanges && (
        <div className={styles.pendingBanner}>
          ⚡ <strong>Unapplied changes</strong> — click Apply to push your edits live.
        </div>
      )}

      {toast && <div className={styles.toast}>✓ {toast}</div>}

      <main className={styles.body}>
        {import.meta.env.DEV && tab !== "settings" && (
          <div className={styles.devBanner}>
            <strong>Dev mode</strong> — connected to local backend
          </div>
        )}

        {tab === "vlans" && (
          <ErrorBoundary label="VLANs">
            <VlansTab state={state} interfaces={interfaces} onReload={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
        {tab === "wan" && (
          <ErrorBoundary label="WAN">
            <WanTab state={state} interfaces={interfaces} onReload={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
        {tab === "dns" && (
          <ErrorBoundary label="DNS">
            <DnsTab state={state} onReload={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
        {tab === "routes" && (
          <ErrorBoundary label="Routes">
            <RoutesTab state={state} onReload={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
        {tab === "firewall" && (
          <ErrorBoundary label="Firewall">
            <FirewallTab state={state} onReload={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
        {tab === "tailscale" && (
          <ErrorBoundary label="Tailscale">
            <TailscaleTab state={state} onReload={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
        {tab === "wireless" && (
          <ErrorBoundary label="Wireless">
            <WirelessTab state={state} onReload={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
        {tab === "diagnostics" && (
          <ErrorBoundary label="Diagnostics">
            <DiagnosticsTab />
          </ErrorBoundary>
        )}
        {tab === "logging" && (
          <ErrorBoundary label="Logging">
            <LoggingTab state={state} onReload={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
        {tab === "snmp" && (
          <ErrorBoundary label="SNMP">
            <SnmpTab state={state} interfaces={interfaces} onReload={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
        {tab === "status" && (
          <ErrorBoundary label="Status">
            <StatusTab />
          </ErrorBoundary>
        )}
        {tab === "preview" && (
          <ErrorBoundary label="Preview">
            <PreviewTab />
          </ErrorBoundary>
        )}
        {tab === "update" && (
          <ErrorBoundary label="Update">
            <UpdateTab />
          </ErrorBoundary>
        )}
        {tab === "settings" && (
          <ErrorBoundary label="Settings">
            <SettingsTab onLogout={handleLogout} onImport={reload} showToast={showToast} />
          </ErrorBoundary>
        )}
      </main>
    </div>
  );
}
