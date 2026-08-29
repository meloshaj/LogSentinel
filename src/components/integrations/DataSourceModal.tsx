import { useState, useEffect, useRef, useCallback } from "react";
import {
  X,
  Copy,
  Check,
  Terminal,
  Container,
  Code2,
  Radio,
  Eye,
  EyeOff,
  Wifi,
  WifiOff,
  Zap,
  ExternalLink,
  ChevronRight,
} from "lucide-react";
import { useTelemetrySocket } from "../../hooks/useTelemetrySocket";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type TabId = "fluent-bit" | "vector" | "python-sdk" | "curl";

interface TabDef {
  id: TabId;
  label: string;
  icon: typeof Terminal;
  description: string;
}

const TABS: TabDef[] = [
  {
    id: "fluent-bit",
    label: "Fluent Bit",
    icon: Radio,
    description: "Kubernetes & host DaemonSet agent",
  },
  {
    id: "vector",
    label: "Vector",
    icon: Container,
    description: "Docker container stdout forwarding",
  },
  {
    id: "python-sdk",
    label: "Python SDK",
    icon: Code2,
    description: "Drop-in logging.Handler",
  },
  {
    id: "curl",
    label: "cURL / REST",
    icon: Terminal,
    description: "Raw HTTP endpoint testing",
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function maskKey(key: string): string {
  if (key.length <= 10) return "••••••••••";
  return key.slice(0, 10) + "••••••••";
}

// ---------------------------------------------------------------------------
// CopyButton
// ---------------------------------------------------------------------------

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* fallback: noop */
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-semibold tracking-wide uppercase transition-all duration-200"
      style={{
        background: copied ? "rgba(63,185,80,0.15)" : "rgba(56,139,253,0.10)",
        color: copied ? "#3fb950" : "#388bfd",
        border: `1px solid ${copied ? "rgba(63,185,80,0.3)" : "rgba(56,139,253,0.2)"}`,
      }}
    >
      {copied ? (
        <>
          <Check className="w-3 h-3" /> Copied!
        </>
      ) : (
        <>
          <Copy className="w-3 h-3" /> Copy
        </>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// CodeBlock
// ---------------------------------------------------------------------------

function CodeBlock({
  code,
  language = "bash",
}: {
  code: string;
  language?: string;
}) {
  return (
    <div className="relative group rounded-lg overflow-hidden border border-[#21262d]">
      <div className="flex items-center justify-between px-3 py-1.5 bg-[#161b22] border-b border-[#21262d]">
        <span
          className="text-[#484f58] uppercase tracking-widest"
          style={{ fontSize: "9px", fontWeight: 700 }}
        >
          {language}
        </span>
        <CopyButton text={code} />
      </div>
      <pre
        className="p-4 overflow-x-auto bg-[#0d1117] text-[#e6edf3]"
        style={{ fontSize: "12px", lineHeight: 1.7, fontFamily: "monospace" }}
      >
        <code>{code}</code>
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// IngestionBeacon — Mini live WebSocket listener
// ---------------------------------------------------------------------------

function IngestionBeacon() {
  const { connectionStatus, eventCount } = useTelemetrySocket();
  const [detected, setDetected] = useState(false);
  const prevCountRef = useRef(eventCount);

  useEffect(() => {
    if (eventCount > prevCountRef.current) {
      setDetected(true);
    }
    prevCountRef.current = eventCount;
  }, [eventCount]);

  const isConnected = connectionStatus === "connected";

  return (
    <div
      className="flex items-center gap-2.5 px-3 py-2.5 rounded-lg border transition-all duration-500"
      style={{
        background: detected
          ? "rgba(63,185,80,0.08)"
          : isConnected
            ? "rgba(56,139,253,0.06)"
            : "rgba(248,81,73,0.06)",
        borderColor: detected
          ? "rgba(63,185,80,0.25)"
          : isConnected
            ? "rgba(56,139,253,0.15)"
            : "rgba(248,81,73,0.2)",
      }}
    >
      <span
        className="relative flex w-2.5 h-2.5"
      >
        {detected && (
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#3fb950] opacity-75" />
        )}
        <span
          className="relative inline-flex rounded-full w-2.5 h-2.5"
          style={{
            background: detected ? "#3fb950" : isConnected ? "#388bfd" : "#f85149",
          }}
        />
      </span>
      <div className="flex flex-col">
        <span
          style={{
            fontSize: "11px",
            fontWeight: 600,
            color: detected ? "#3fb950" : isConnected ? "#388bfd" : "#f85149",
          }}
        >
          {detected
            ? "Logs Detected!"
            : isConnected
              ? "Listening for ingestion…"
              : "WebSocket Disconnected"}
        </span>
        <span className="text-[#484f58]" style={{ fontSize: "10px" }}>
          {detected
            ? `${eventCount} event${eventCount !== 1 ? "s" : ""} received`
            : isConnected
              ? "Waiting for first log batch"
              : "Attempting to reconnect…"}
        </span>
      </div>
      {isConnected ? (
        <Wifi className="w-3.5 h-3.5 text-[#484f58] ml-auto" />
      ) : (
        <WifiOff className="w-3.5 h-3.5 text-[#484f58] ml-auto" />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab Content Components
// ---------------------------------------------------------------------------

function FluentBitTab({ apiKey, showKey }: { apiKey: string; showKey: boolean }) {
  const displayKey = showKey ? apiKey : maskKey(apiKey);

  const configSnippet = `[SERVICE]
    Flush         1
    Log_Level     info

[INPUT]
    Name              tail
    Path              /var/log/containers/*.log
    Parser            docker
    Tag               kube.*
    Mem_Buf_Limit     50MB
    Skip_Long_Lines   On

[FILTER]
    Name                kubernetes
    Match               kube.*
    Merge_Log           On
    Keep_Log            Off

[OUTPUT]
    Name          http
    Match         *
    Host          backend
    Port          8000
    URI           /api/v1/ingest/bulk
    Header        X-API-Key ${displayKey}
    Header        Content-Type application/x-ndjson
    Format        json_lines
    Compress      gzip
    Retry_Limit   False`;

  const composeSnippet = `# docker-compose.collector.yml
docker compose -f deploy/collectors/fluent-bit/docker-compose.collector.yml up -d`;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 p-3 rounded-lg bg-[#161b22] border border-[#21262d]">
        <Zap className="w-4 h-4 text-[#ffa657] mt-0.5 shrink-0" />
        <p className="text-[#8b949e]" style={{ fontSize: "12px", lineHeight: 1.6 }}>
          Fluent Bit runs as a DaemonSet to tail container logs, inject Kubernetes
          metadata, and stream gzip-compressed NDJSON batches to LogSentinel.
        </p>
      </div>

      <div>
        <h4 className="text-[#e6edf3] mb-2" style={{ fontSize: "12px", fontWeight: 600 }}>
          1. Configuration <span className="text-[#484f58] font-normal">(fluent-bit.conf)</span>
        </h4>
        <CodeBlock code={configSnippet} language="ini" />
      </div>

      <div>
        <h4 className="text-[#e6edf3] mb-2" style={{ fontSize: "12px", fontWeight: 600 }}>
          2. Quick Start
        </h4>
        <CodeBlock code={composeSnippet} language="bash" />
      </div>
    </div>
  );
}

function VectorTab({ apiKey, showKey }: { apiKey: string; showKey: boolean }) {
  const displayKey = showKey ? apiKey : maskKey(apiKey);

  const vectorSnippet = `# vector.yaml
sources:
  docker_logs:
    type: "docker_logs"
    include_containers: ["*"]

transforms:
  normalize_logs:
    type: "remap"
    inputs: ["docker_logs"]
    source: |
      .service_name = .container.name
      .timestamp = .timestamp || now()
      parsed_json, err = parse_json(.message)
      if err == null {
        .message = parsed_json.message || .message
        .level = parsed_json.level || "INFO"
        .trace_id = parsed_json.trace_id
      } else {
        .level = "INFO"
      }

sinks:
  logsentinel:
    type: "http"
    inputs: ["normalize_logs"]
    uri: "http://backend:8000/api/v1/ingest/bulk"
    method: "post"
    request:
      headers:
        X-API-Key: "${displayKey}"
    encoding:
      codec: "ndjson"
    compression: "gzip"
    batch:
      max_events: 500
      timeout_secs: 1.0`;

  const runSnippet = `docker run -d --name logsentinel-vector \\
  -v /var/run/docker.sock:/var/run/docker.sock:ro \\
  -v $(pwd)/vector.yaml:/etc/vector/vector.yaml:ro \\
  timberio/vector:0.38.0-alpine`;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 p-3 rounded-lg bg-[#161b22] border border-[#21262d]">
        <Zap className="w-4 h-4 text-[#a371f7] mt-0.5 shrink-0" />
        <p className="text-[#8b949e]" style={{ fontSize: "12px", lineHeight: 1.6 }}>
          Vector reads container stdout directly from the Docker socket, uses VRL
          to normalize logs, and ships them as gzip-compressed NDJSON batches.
        </p>
      </div>

      <div>
        <h4 className="text-[#e6edf3] mb-2" style={{ fontSize: "12px", fontWeight: 600 }}>
          1. Configuration
        </h4>
        <CodeBlock code={vectorSnippet} language="yaml" />
      </div>

      <div>
        <h4 className="text-[#e6edf3] mb-2" style={{ fontSize: "12px", fontWeight: 600 }}>
          2. Launch
        </h4>
        <CodeBlock code={runSnippet} language="bash" />
      </div>
    </div>
  );
}

function PythonSdkTab({ apiKey, showKey }: { apiKey: string; showKey: boolean }) {
  const displayKey = showKey ? apiKey : maskKey(apiKey);
  const apiBase = (import.meta.env.VITE_API_URL || window.location.origin).replace(/\/+$/, "");

  const installSnippet = `# Copy the handler into your project
cp sdk/python/logsentinel_logger.py your_service/`;

  const usageSnippet = `import logging
from logsentinel_logger import LogSentinelHandler

handler = LogSentinelHandler(
    api_key="${displayKey}",
    service_name="payment-gateway",
    endpoint="${apiBase}/api/v1/ingest/bulk",
    batch_size=100,
    flush_interval_seconds=1.0,
)

logger = logging.getLogger("my_service")
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Emit logs — they are batched and shipped in the background
logger.info("Order processed", extra={
    "trace_id": "abc-123",
    "order_id": 42,
})`;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 p-3 rounded-lg bg-[#161b22] border border-[#21262d]">
        <Zap className="w-4 h-4 text-[#79c0ff] mt-0.5 shrink-0" />
        <p className="text-[#8b949e]" style={{ fontSize: "12px", lineHeight: 1.6 }}>
          Zero-dependency Python <code className="text-[#e6edf3] bg-[#21262d] px-1 py-0.5 rounded text-[11px]">logging.Handler</code> that
          batches and streams logs via a background daemon thread. Supports{" "}
          <code className="text-[#e6edf3] bg-[#21262d] px-1 py-0.5 rounded text-[11px]">trace_id</code>,{" "}
          <code className="text-[#e6edf3] bg-[#21262d] px-1 py-0.5 rounded text-[11px]">span_id</code>,
          and arbitrary metadata via <code className="text-[#e6edf3] bg-[#21262d] px-1 py-0.5 rounded text-[11px]">extra={"{...}"}</code>.
        </p>
      </div>

      <div>
        <h4 className="text-[#e6edf3] mb-2" style={{ fontSize: "12px", fontWeight: 600 }}>
          1. Install
        </h4>
        <CodeBlock code={installSnippet} language="bash" />
      </div>

      <div>
        <h4 className="text-[#e6edf3] mb-2" style={{ fontSize: "12px", fontWeight: 600 }}>
          2. Usage
        </h4>
        <CodeBlock code={usageSnippet} language="python" />
      </div>
    </div>
  );
}

function CurlTab({ apiKey, showKey }: { apiKey: string; showKey: boolean }) {
  const displayKey = showKey ? apiKey : maskKey(apiKey);
  const apiBase = (import.meta.env.VITE_API_URL || window.location.origin).replace(/\/+$/, "");

  const curlSnippet = `curl -X POST ${apiBase}/api/v1/ingest/bulk \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: ${displayKey}" \\
  -d '{
    "logs": [
      {
        "timestamp": "${new Date().toISOString()}",
        "service_name": "api-gateway",
        "level": "INFO",
        "message": "Health check passed",
        "trace_id": "abc-123-def-456",
        "metadata": { "region": "us-east-1" }
      },
      {
        "service_name": "payment-service",
        "level": "ERROR",
        "message": "Connection pool exhausted",
        "metadata": { "pool_size": 50, "active": 50 }
      }
    ]
  }'`;

  const ndjsonSnippet = `curl -X POST ${apiBase}/api/v1/ingest/bulk \\
  -H "Content-Type: application/x-ndjson" \\
  -H "X-API-Key: ${displayKey}" \\
  -d '{"service_name":"auth-svc","level":"INFO","message":"User login succeeded"}
{"service_name":"auth-svc","level":"WARN","message":"Rate limit approaching"}'`;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 p-3 rounded-lg bg-[#161b22] border border-[#21262d]">
        <Zap className="w-4 h-4 text-[#3fb950] mt-0.5 shrink-0" />
        <p className="text-[#8b949e]" style={{ fontSize: "12px", lineHeight: 1.6 }}>
          Test ingestion directly with <code className="text-[#e6edf3] bg-[#21262d] px-1 py-0.5 rounded text-[11px]">cURL</code>.
          The endpoint accepts standard JSON arrays and NDJSON. Response returns HTTP 202 with ingested count.
        </p>
      </div>

      <div>
        <h4 className="text-[#e6edf3] mb-2" style={{ fontSize: "12px", fontWeight: 600 }}>
          1. JSON Array
        </h4>
        <CodeBlock code={curlSnippet} language="bash" />
      </div>

      <div>
        <h4 className="text-[#e6edf3] mb-2" style={{ fontSize: "12px", fontWeight: 600 }}>
          2. Newline-Delimited JSON (NDJSON)
        </h4>
        <CodeBlock code={ndjsonSnippet} language="bash" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DataSourceModal
// ---------------------------------------------------------------------------

interface DataSourceModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function DataSourceModal({
  isOpen,
  onClose,
}: DataSourceModalProps) {
  const [apiKey, setApiKey] = useState("Loading...");

  useEffect(() => {
    fetch("/api/auth/api-key", {
      headers: { Authorization: `Bearer ${localStorage.getItem("token")}` }
    })
      .then(res => res.json())
      .then(data => setApiKey(data.api_key || "Error loading key"))
      .catch(() => setApiKey("Error loading key"));
  }, []);
  const [activeTab, setActiveTab] = useState<TabId>("fluent-bit");
  const [showKey, setShowKey] = useState(false);
  const [isAnimating, setIsAnimating] = useState(false);
  const backdropRef = useRef<HTMLDivElement>(null);

  // Open / close animation
  useEffect(() => {
    if (isOpen) {
      setIsAnimating(true);
    }
  }, [isOpen]);

  const handleClose = useCallback(() => {
    setIsAnimating(false);
    setTimeout(onClose, 200);
  }, [onClose]);

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [isOpen, handleClose]);

  // Close on backdrop click
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === backdropRef.current) handleClose();
  };

  if (!isOpen && !isAnimating) return null;

  const TabContent = () => {
    switch (activeTab) {
      case "fluent-bit":
        return <FluentBitTab apiKey={apiKey} showKey={showKey} />;
      case "vector":
        return <VectorTab apiKey={apiKey} showKey={showKey} />;
      case "python-sdk":
        return <PythonSdkTab apiKey={apiKey} showKey={showKey} />;
      case "curl":
        return <CurlTab apiKey={apiKey} showKey={showKey} />;
    }
  };

  return (
    <div
      ref={backdropRef}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{
        backdropFilter: isAnimating && isOpen ? "blur(8px)" : "blur(0px)",
        background:
          isAnimating && isOpen ? "rgba(1,4,9,0.75)" : "rgba(1,4,9,0)",
        transition: "all 200ms ease-out",
      }}
    >
      <div
        className="relative w-full max-w-3xl max-h-[90vh] flex flex-col rounded-2xl overflow-hidden border border-[#30363d] shadow-2xl"
        style={{
          background:
            "linear-gradient(145deg, rgba(22,27,34,0.98) 0%, rgba(13,17,23,0.99) 100%)",
          boxShadow:
            "0 0 0 1px rgba(48,54,61,0.6), 0 16px 70px rgba(1,4,9,0.7), 0 0 40px rgba(56,139,253,0.04)",
          opacity: isAnimating && isOpen ? 1 : 0,
          transform:
            isAnimating && isOpen
              ? "scale(1) translateY(0)"
              : "scale(0.96) translateY(8px)",
          transition: "all 200ms ease-out",
        }}
      >
        {/* ---- Header ---- */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#21262d] shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-[#1f6feb] to-[#388bfd]">
              <Zap className="w-4 h-4 text-white" />
            </div>
            <div>
              <h2
                className="text-[#e6edf3]"
                style={{ fontSize: "15px", fontWeight: 700 }}
              >
                Add Data Source
              </h2>
              <p className="text-[#484f58]" style={{ fontSize: "11px" }}>
                Connect your services to the LogSentinel ingestion pipeline
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* API Key visibility toggle */}
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[#7d8590] hover:text-[#e6edf3] bg-[#0d1117] border border-[#21262d] hover:border-[#30363d] transition-colors"
              style={{ fontSize: "11px" }}
              title={showKey ? "Hide API key" : "Reveal API key"}
            >
              {showKey ? (
                <EyeOff className="w-3.5 h-3.5" />
              ) : (
                <Eye className="w-3.5 h-3.5" />
              )}
              {showKey ? "Hide Key" : "Show Key"}
            </button>

            <button
              type="button"
              onClick={handleClose}
              className="flex items-center justify-center w-8 h-8 rounded-lg text-[#7d8590] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors"
              aria-label="Close modal"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* ---- Live Ingestion Beacon ---- */}
        <div className="px-6 pt-4 shrink-0">
          <IngestionBeacon />
        </div>

        {/* ---- Tab Navigation ---- */}
        <div className="flex gap-1 px-6 pt-4 pb-0 shrink-0 overflow-x-auto">
          {TABS.map((tab) => {
            const isActive = activeTab === tab.id;
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className="flex items-center gap-2 px-3 py-2 rounded-t-lg transition-all duration-150 whitespace-nowrap shrink-0"
                style={{
                  background: isActive ? "rgba(56,139,253,0.10)" : "transparent",
                  borderBottom: isActive ? "2px solid #388bfd" : "2px solid transparent",
                  color: isActive ? "#e6edf3" : "#7d8590",
                  fontSize: "12px",
                  fontWeight: isActive ? 600 : 400,
                }}
              >
                <Icon className="w-3.5 h-3.5" />
                {tab.label}
              </button>
            );
          })}
        </div>

        <div className="w-full h-px bg-[#21262d] shrink-0" />

        {/* ---- Tab Content ---- */}
        <div className="flex-1 overflow-y-auto px-6 py-5 min-h-0">
          <TabContent />
        </div>

        {/* ---- Footer ---- */}
        <div className="flex items-center justify-between px-6 py-3 border-t border-[#21262d] bg-[#0d1117]/60 shrink-0">
          <a
            href="https://github.com/meloshaj/LogSentinel"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-[#484f58] hover:text-[#8b949e] transition-colors"
            style={{ fontSize: "11px" }}
          >
            <ExternalLink className="w-3 h-3" />
            Full Documentation
          </a>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleClose}
              className="px-4 py-1.5 rounded-lg text-[#7d8590] hover:text-[#e6edf3] bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] transition-colors"
              style={{ fontSize: "12px", fontWeight: 500 }}
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trigger button — drop into any page or navbar
// ---------------------------------------------------------------------------

export function AddDataSourceButton({
  variant = "primary",
}: {
  variant?: "primary" | "compact";
}) {
  const [isOpen, setIsOpen] = useState(false);

  if (variant === "compact") {
    return (
      <>
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-[#161b22] border border-[#21262d] text-[#7d8590] hover:text-[#e6edf3] hover:border-[#30363d] transition-colors"
          style={{ fontSize: "11px", fontWeight: 500 }}
        >
          <Zap className="w-3 h-3 text-[#388bfd]" />
          Add Source
        </button>
        <DataSourceModal isOpen={isOpen} onClose={() => setIsOpen(false)} />
      </>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-all duration-200"
        style={{
          fontSize: "12px",
          fontWeight: 600,
          background: "linear-gradient(135deg, #1f6feb 0%, #388bfd 100%)",
          color: "#ffffff",
          border: "1px solid rgba(56,139,253,0.4)",
          boxShadow: "0 0 20px rgba(56,139,253,0.15), 0 2px 8px rgba(0,0,0,0.3)",
        }}
      >
        <Zap className="w-3.5 h-3.5" />
        Add Data Source
        <ChevronRight className="w-3 h-3 opacity-60" />
      </button>
      <DataSourceModal isOpen={isOpen} onClose={() => setIsOpen(false)} />
    </>
  );
}
