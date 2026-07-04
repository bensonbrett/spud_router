// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useState } from "react";
import styles from "./CodeBlock.module.css";

export function CodeBlock({ content }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard?.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className={styles.wrapper}>
      <button className={styles.copyBtn} onClick={copy}>
        {copied ? "✓" : "copy"}
      </button>
      <pre className={styles.pre}>{content}</pre>
    </div>
  );
}
