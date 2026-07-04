// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import styles from "./Btn.module.css";

/**
 * Btn — primary action button.
 *
 * variant: "primary" | "danger" | "ghost"
 * small:   reduced padding
 * full:    100% width
 */
export function Btn({ children, onClick, variant = "primary", disabled = false, small = false, full = false }) {
  return (
    <button
      className={styles.btn}
      data-variant={variant}
      data-size={small ? "small" : undefined}
      data-full={full || undefined}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
