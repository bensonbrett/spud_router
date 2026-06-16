import { Component } from "react";
import styles from "./ErrorBoundary.module.css";

/**
 * ErrorBoundary — catches render errors in any child component tree.
 *
 * Usage:
 *   <ErrorBoundary label="Firewall">
 *     <FirewallTab ... />
 *   </ErrorBoundary>
 *
 * The label is shown in the error card so the user knows which section
 * failed without seeing a raw stack trace.
 */
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // In production this would go to an error reporting service.
    // For a self-hosted homelab tool, console is fine.
    console.error("[spud-router] Render error in", this.props.label, error, info);
  }

  handleReset() {
    this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return (
        <div className={styles.card}>
          <div className={styles.icon}>⚠</div>
          <h3 className={styles.title}>
            {this.props.label
              ? `Something went wrong in ${this.props.label}`
              : "Something went wrong"}
          </h3>
          <p className={styles.message}>
            {this.state.error.message || "An unexpected error occurred."}
          </p>
          <details className={styles.details}>
            <summary>Stack trace</summary>
            <pre className={styles.stack}>{this.state.error.stack}</pre>
          </details>
          <button className={styles.resetBtn} onClick={() => this.handleReset()}>
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

/**
 * withErrorBoundary — HOC convenience wrapper.
 *
 *   const SafeFirewallTab = withErrorBoundary(FirewallTab, "Firewall");
 */
export function withErrorBoundary(WrappedComponent, label) {
  const displayName = label || WrappedComponent.displayName || WrappedComponent.name;

  function WithBoundary(props) {
    return (
      <ErrorBoundary label={displayName}>
        <WrappedComponent {...props} />
      </ErrorBoundary>
    );
  }

  WithBoundary.displayName = `withErrorBoundary(${displayName})`;
  return WithBoundary;
}
