import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Global React error boundary.
 *
 * Catches unhandled render errors anywhere in the component tree and presents
 * a clean recovery screen instead of unmounting to a blank page.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary] Uncaught render error:", error, info.componentStack);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div
        style={{
          minHeight: "100vh",
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#060c18",
          fontFamily:
            'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
          color: "#e6edf3",
          padding: "24px",
        }}
      >
        <div
          style={{
            maxWidth: 480,
            width: "100%",
            textAlign: "center",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 20,
          }}
        >
          {/* Icon */}
          <div
            style={{
              width: 56,
              height: 56,
              borderRadius: "50%",
              background: "rgba(248,81,73,0.15)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 28,
            }}
          >
            ⚠
          </div>

          <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0, color: "#e6edf3" }}>
            Something went wrong
          </h1>

          <p
            style={{
              fontSize: 13,
              color: "#7d8590",
              lineHeight: 1.6,
              margin: 0,
            }}
          >
            LogSentinel encountered an unexpected error. You can reload the application
            to recover. If the issue persists, please contact your administrator.
          </p>

          {/* Error detail */}
          {this.state.error && (
            <pre
              style={{
                width: "100%",
                textAlign: "left",
                fontSize: 11,
                color: "#f85149",
                background: "rgba(248,81,73,0.08)",
                border: "1px solid rgba(248,81,73,0.2)",
                borderRadius: 8,
                padding: "12px 16px",
                margin: 0,
                overflow: "auto",
                maxHeight: 120,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {this.state.error.message}
            </pre>
          )}

          <button
            onClick={this.handleReload}
            style={{
              padding: "10px 24px",
              fontSize: 13,
              fontWeight: 600,
              color: "#ffffff",
              background: "#388bfd",
              border: "none",
              borderRadius: 8,
              cursor: "pointer",
              fontFamily: "inherit",
              transition: "background 0.15s ease",
            }}
            onMouseOver={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "#58a6ff";
            }}
            onMouseOut={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "#388bfd";
            }}
          >
            Reload Application
          </button>
        </div>
      </div>
    );
  }
}
