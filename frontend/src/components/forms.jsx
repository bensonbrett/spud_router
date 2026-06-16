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

export function Select({ value, onChange, options }) {
  return (
    <select
      className={styles.select}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

export function Field({ label, children, help }) {
  return (
    <div className={styles.field}>
      <label className={styles.fieldLabel}>{label}</label>
      {children}
      {help && <p className={styles.fieldHelp}>{help}</p>}
    </div>
  );
}

export function ErrMsg({ msg }) {
  return msg ? <div className={styles.errMsg}>⚠ {msg}</div> : null;
}

export function OkMsg({ msg }) {
  return msg ? <div className={styles.okMsg}>✓ {msg}</div> : null;
}
