// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import styles from "./Toggle.module.css";

export function Toggle({ value, onChange, label, disabled = false, id }) {
  const toggle = () => {
    if (!disabled) onChange(!value);
  };

  return (
    <label className={styles.label} data-disabled={disabled}>
      <input
        id={id}
        className={styles.input}
        type="checkbox"
        role="switch"
        checked={Boolean(value)}
        disabled={disabled}
        onChange={toggle}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            toggle();
          }
        }}
      />
      <span
        className={styles.track}
        data-checked={Boolean(value)}
        aria-hidden="true"
      >
        <span className={styles.thumb} />
      </span>
      <span className={styles.text}>{label}</span>
    </label>
  );
}
