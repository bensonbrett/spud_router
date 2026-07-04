// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import styles from "./Toggle.module.css";

export function Toggle({ value, onChange, label }) {
  return (
    <label className={styles.label}>
      <div
        className={styles.track}
        data-checked={value}
        onClick={() => onChange(!value)}
      >
        <div className={styles.thumb} />
      </div>
      <span className={styles.text}>{label}</span>
    </label>
  );
}
