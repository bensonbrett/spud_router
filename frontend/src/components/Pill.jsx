// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import styles from "./Pill.module.css";

/**
 * Pill — small status badge.
 * variant: "success" | "warning" | "danger" | "accent" | "muted"
 */
export function Pill({ variant, children }) {
  return (
    <span className={styles.pill} data-variant={variant}>
      {children}
    </span>
  );
}
