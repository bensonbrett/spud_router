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
