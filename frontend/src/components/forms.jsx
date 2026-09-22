// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { cloneElement, isValidElement, useId } from "react";
import styles from "./forms.module.css";

export function Input({ value, onChange, disabled, ...props }) {
  return (
    <input
      className={styles.input}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      {...props}
    />
  );
}

export function Textarea({ value, onChange, disabled, rows = 6, ...props }) {
  return (
    <textarea
      className={styles.textarea}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      rows={rows}
      {...props}
    />
  );
}

export function Select({ value, onChange, options, disabled, ...props }) {
  return (
    <select
      className={styles.select}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      {...props}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

export function Field({ label, children, help }) {
  const generatedId = useId();
  const helpId = useId();
  const isControl = isValidElement(children) && (
    children.type === Input ||
    children.type === Select ||
    children.type === Textarea ||
    ["input", "select", "textarea"].includes(children.type)
  );
  const controlId = isControl ? children.props.id || generatedId : undefined;
  const describedBy = children?.props?.["aria-describedby"];
  const control = isControl
    ? cloneElement(children, {
      id: controlId,
      "aria-describedby": help
        ? [describedBy, helpId].filter(Boolean).join(" ")
        : describedBy,
    })
    : children;

  return (
    <div className={styles.field}>
      <label className={styles.fieldLabel} htmlFor={controlId}>{label}</label>
      {control}
      {help && <p id={helpId} className={styles.fieldHelp}>{help}</p>}
    </div>
  );
}

export function ErrMsg({ msg }) {
  return msg ? <div className={styles.errMsg} role="alert">⚠ {msg}</div> : null;
}

export function OkMsg({ msg }) {
  return msg ? <div className={styles.okMsg} role="status">✓ {msg}</div> : null;
}
