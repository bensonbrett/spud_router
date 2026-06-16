import { useState } from "react";
import { POST } from "../api.js";
import { Btn, ErrMsg, Field, Input } from "../components/index.js";
import styles from "./LoginScreen.module.css";

export function LoginScreen({ onLogin }) {
  const [user, setUser] = useState("admin");
  const [pass, setPass] = useState("");
  const [err,  setErr]  = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setErr("");
    try {
      const res = await POST("/api/auth/login", { username: user, password: pass });
      sessionStorage.setItem("spud_token", res.token);
      onLogin();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.loginPage}>
      <div className={styles.loginBox}>
        <div className={styles.loginLogoWrap}>
          <div className={styles.loginEmoji}>🥔</div>
          <div className={styles.loginTitle}>
            spud<span>-router</span>
          </div>
          <div className={styles.loginSubtitle}>Sign in to continue</div>
        </div>
        <div className={styles.loginCard}>
          <ErrMsg msg={err} />
          <Field label="Username">
            <Input value={user} onChange={setUser} autoComplete="username" />
          </Field>
          <Field label="Password">
            <Input
              value={pass}
              onChange={setPass}
              type="password"
              autoComplete="current-password"
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </Field>
          {import.meta.env.DEV && (
            <p className={styles.loginHint}>Demo: admin / spudrouter</p>
          )}
          <Btn onClick={submit} disabled={busy} full>
            {busy ? "Signing in…" : "Sign In"}
          </Btn>
        </div>
      </div>
    </div>
  );
}

// ── VLANs tab ─────────────────────────────────────────────────────────────────
const defVlan = {
  vlan_id: "", name: "", interface: "eth0", ip_address: "",
  prefix_len: "24", dhcp_enabled: true, dhcp_start: "",
  dhcp_end: "", dhcp_lease: "12h", isolate: false,
};
