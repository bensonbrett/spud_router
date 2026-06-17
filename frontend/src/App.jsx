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
import { WirelessTab }  from "./tabs/WirelessTab.jsx";
import styles from "./App.module.css";

const TABS = [
  { id: "vlans",     label: "VLANs",     icon: "⫿" },
  { id: "wan",       label: "WAN",       icon: "🌐" },
  { id: "dns",       label: "DNS",       icon: "◈"  },
  { id: "routes",    label: "Routes",    icon: "↗"  },
  { id: "firewall",  label: "Firewall",  icon: "🛡" },
  { id: "tailscale", label: "Tailscale", icon: "🔒" },
  { id: "wireless",  label: "Wireless",  icon: "📶" },
  { id: "status",    label: "Status",    icon: "◉"  },
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

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2800);
  };

  const reload = useCallback(async () => {
    const [s, ifaces] = await Promise.all([GET("/api/state"), GET("/api/interfaces")]);
    setState(s);
    setInterfaces(ifaces);
  }, []);

  // On mount, probe the API to see if the session cookie is still valid.
  // This is the only auth-state check — no sessionStorage involved.
  useEffect(() => {
    GET("/api/state")
      .then((s) => { setState(s); setAuthed(true); })
      .catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    if (authed === true) GET("/api/interfaces").then(setInterfaces).catch(() => {});
  }, [authed]);

  const handleApply = async () => {
    setApplying(true);
    setApplySteps([]);
    try {
      const res = await POST("/api/apply", { dry_run: false });
      setApplySteps(res.steps || []);
      showToast("Config applied!");
    } catch (e) {
      showToast("Apply failed: " + e.message);
    } finally {
      setApplying(false);
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
            <WirelessTab state={state} />
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
            <SettingsTab onLogout={handleLogout} onImport={reload} />
          </ErrorBoundary>
        )}
      </main>
    </div>
  );
}
