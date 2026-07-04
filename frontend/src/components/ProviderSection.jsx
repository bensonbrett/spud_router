// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState } from "react";
import styles from "./ProviderSection.module.css";

/**
 * Collapsible container for one VPN provider's section on the VPN tab —
 * each provider (Tailscale, WireGuard, Nebula) gets its own independent
 * ProviderSection so enabling/configuring one never requires touching or
 * even seeing the others.
 */
export function ProviderSection({ title, statusLine, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={styles.section}>
      <button className={styles.header} onClick={() => setOpen((o) => !o)} type="button">
        <span className={styles.chevron} data-open={open}>▶</span>
        <span className={styles.title}>{title}</span>
        {statusLine && <span className={styles.statusLine}>{statusLine}</span>}
      </button>
      {open && <div className={styles.body}>{children}</div>}
    </div>
  );
}
