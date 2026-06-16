import styles from "./Row.module.css";

/**
 * Row — a list item with a label, optional subtitle, badge slots, and an action slot.
 * Used throughout VLAN, DNS, Routes, Firewall lists.
 */
export function Row({ left, sub, badges = [], right }) {
  return (
    <div className={styles.row}>
      <div className={styles.content}>
        <div className={styles.label}>{left}</div>
        {sub && <div className={styles.sub}>{sub}</div>}
      </div>
      <div className={styles.actions}>
        {badges}
        {right}
      </div>
    </div>
  );
}
