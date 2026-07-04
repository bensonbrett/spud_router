// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import styles from "./Card.module.css";

export function Card({ title, children, right }) {
  return (
    <div className={styles.card}>
      {title && (
        <div className={styles.header}>
          <span className={styles.title}>{title}</span>
          {right && <div className={styles.right}>{right}</div>}
        </div>
      )}
      <div className={styles.body}>{children}</div>
    </div>
  );
}
