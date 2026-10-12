import { Bell, Key, RefreshCw, Save, Sliders } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { FeatureFlag, useFeatureFlag } from "../components/common/FeatureFlag";
import { getApiUrl } from "../config/api";
import { fetchAuthenticated } from "../utils/auth";

function Section({ title, icon: Icon, children }: { title: string; icon: React.ElementType; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
        <Icon className="w-4 h-4 text-[#7d8590]" />
        <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>{title}</span>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function Field({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-3 border-b border-[#21262d] last:border-0">
      <div>
        <div className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 500 }}>{label}</div>
        {description && <div className="text-[#484f58] mt-0.5" style={{ fontSize: "10px" }}>{description}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function Toggle({ on, onChange, disabled = false, label }: { on: boolean; onChange: (value: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-label={label}
      aria-checked={on}
      onClick={() => onChange(!on)}
      disabled={disabled}
      className={`relative w-10 h-5 rounded-full transition-colors ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
      style={{ background: on ? "#1f6feb" : "#21262d" }}
    >
      <span
        className="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform"
        style={{ transform: on ? "translateX(20px)" : "translateX(0)" }}
      />
    </button>
  );
}


export function SettingsPage() {
  const [apiKey, setApiKey] = useState("Loading...");
  const [activeKeyId, setActiveKeyId] = useState<number | null>(null);
  const [oneTimeKey, setOneTimeKey] = useState<string | null>(null);
  const [keyActionError, setKeyActionError] = useState<string | null>(null);
  const enableEdit = useFeatureFlag('ENABLE_SETTINGS_EDIT');
  const [settings, setSettings] = useState({ critical_alerts: true, anomaly_threshold: 0.85, error_rate_threshold: 0.10, latency_p95_ms: 600 });
  const [settingsState, setSettingsState] = useState<"loading" | "ready" | "saving" | "saved" | "error">("loading");

  const loadApiKey = useCallback(() => {
    return fetchAuthenticated(getApiUrl("/api/auth/api-key"))
      .then(res => {
        if (!res.ok) throw new Error("Unable to load API keys");
        return res.json();
      })
      .then(data => {
        const active = (data.keys || []).find((key: { id: number; revoked?: boolean }) => !key.revoked);
        setActiveKeyId(active?.id ?? null);
        setApiKey(active ? `${active.key_prefix}… (secret shown only at creation)` : "No active keys");
      });
  }, []);

  useEffect(() => {
    loadApiKey().catch(() => setApiKey("Error loading key"));
    fetchAuthenticated("/api/v1/settings").then(async (response) => {
      if (!response.ok) throw new Error();
      setSettings(await response.json());
      setSettingsState("ready");
    }).catch(() => setSettingsState("error"));
  }, [loadApiKey]);

  const saveSettings = async () => {
    setSettingsState("saving");
    try {
      const response = await fetchAuthenticated("/api/v1/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
      if (!response.ok) throw new Error();
      setSettings(await response.json());
      setSettingsState("saved");
    } catch { setSettingsState("error"); }
  };

  const createKey = async () => {
    setKeyActionError(null);
    try {
      const params = new URLSearchParams({ name: "ingestion-key", expires_in_days: "90" });
      const response = await fetchAuthenticated(getApiUrl("/api/auth/api-key") + "?" + params.toString(), { method: "POST" });
      if (!response.ok) throw new Error("Unable to create API key");
      const data = await response.json();
      setOneTimeKey(data.api_key);
      await loadApiKey();
    } catch {
      setKeyActionError("Unable to create API key");
    }
  };

  const rotateKey = async () => {
    if (activeKeyId === null) return;
    setKeyActionError(null);
    try {
      const response = await fetchAuthenticated(getApiUrl(`/api/auth/api-key/${activeKeyId}/rotate`), { method: "POST" });
      if (!response.ok) throw new Error("Unable to rotate API key");
      const data = await response.json();
      setOneTimeKey(data.api_key);
      await loadApiKey();
    } catch {
      setKeyActionError("Unable to rotate API key");
    }
  };

  const revokeKey = async () => {
    if (activeKeyId === null) return;
    setKeyActionError(null);
    try {
      const response = await fetchAuthenticated(getApiUrl(`/api/auth/api-key/${activeKeyId}`), { method: "DELETE" });
      if (!response.ok) throw new Error("Unable to revoke API key");
      setOneTimeKey(null);
      await loadApiKey();
    } catch {
      setKeyActionError("Unable to revoke API key");
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-[#e6edf3]" style={{ fontSize: "18px", fontWeight: 700 }}>Settings</h1>
        <p className="text-[#7d8590] mt-0.5" style={{ fontSize: "12px" }}>Configure LogSentinel for your organization</p>
      </div>

      {/* API Keys */}
      <Section title="API Keys" icon={Key}>
        <div className="space-y-3">
          <div className="flex items-center gap-2 p-3 rounded-lg bg-[#0d1117] border border-[#21262d]">
            <code className="flex-1 text-[#7d8590]" style={{ fontSize: "12px", fontFamily: "monospace" }}>
              {apiKey}
            </code>
          </div>
          {oneTimeKey && (
            <div className="p-3 rounded-lg bg-[#1f6feb]/10 border border-[#1f6feb]/30">
              <div className="text-[#388bfd] mb-1" style={{ fontSize: "11px", fontWeight: 600 }}>Copy this key now</div>
              <code className="block break-all text-[#e6edf3]" style={{ fontSize: "11px", fontFamily: "monospace" }}>{oneTimeKey}</code>
              <div className="text-[#7d8590] mt-1" style={{ fontSize: "10px" }}>It will not be shown again.</div>
            </div>
          )}
          {keyActionError && <div className="text-red-400" style={{ fontSize: "11px" }}>{keyActionError}</div>}
          <FeatureFlag flag="ENABLE_SETTINGS_EDIT">
            <div className="flex gap-2">
              <button onClick={rotateKey} disabled={activeKeyId === null} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors disabled:opacity-50" style={{ fontSize: "11px" }}>
                <RefreshCw className="w-3 h-3" /> Rotate key
              </button>
              <button onClick={createKey} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#1f6feb]/20 text-[#388bfd] hover:bg-[#1f6feb]/30 transition-colors border border-[#1f6feb]/30" style={{ fontSize: "11px" }}>
                <Key className="w-3 h-3" /> Generate new key
              </button>
              <button onClick={revokeKey} disabled={activeKeyId === null} className="px-3 py-1.5 rounded-lg text-red-400 bg-red-400/10 hover:bg-red-400/20 disabled:opacity-50" style={{ fontSize: "11px" }}>
                Revoke
              </button>
            </div>
          </FeatureFlag>
          <div className="text-[#484f58]" style={{ fontSize: "10px" }}>
            Use this key to authenticate LogSentinel API requests. Keep it secret - it has full access to your workspace.
          </div>
        </div>
      </Section>

      {/* Notifications */}
      <Section title="Notification Settings" icon={Bell}>
        <Field label="Critical Alerts" description="Get notified when anomaly score exceeds 0.85">
          <Toggle label="Critical alerts" on={settings.critical_alerts} onChange={(value) => setSettings((current) => ({ ...current, critical_alerts: value }))} disabled={!enableEdit || settingsState === "loading"} />
        </Field>
        <p className="text-[#7d8590] text-xs py-3">Provider destinations and scheduled digests are configured by administrators outside this screen.</p>
      </Section>

      {/* Thresholds */}
      <Section title="Detection Thresholds" icon={Sliders}>
        <Field label="Anomaly Score Threshold" description="Score above which an alert is triggered">
          <div className="flex items-center gap-2">
            <input aria-label="Anomaly score threshold" type="range" min="0" max="100" value={Math.round(settings.anomaly_threshold * 100)} onChange={(event) => setSettings((current) => ({ ...current, anomaly_threshold: Number(event.target.value) / 100 }))} className={`w-24 accent-[#388bfd] ${!enableEdit ? 'opacity-50 cursor-not-allowed' : ''}`} disabled={!enableEdit} />
            <span className="text-[#7d8590] w-8 text-right" style={{ fontSize: "12px" }}>{settings.anomaly_threshold.toFixed(2)}</span>
          </div>
        </Field>
        <Field label="Error Rate Threshold" description="Service error rate that triggers warning">
          <div className="flex items-center gap-2">
            <input aria-label="Error rate threshold" type="range" min="0" max="100" value={Math.round(settings.error_rate_threshold * 100)} onChange={(event) => setSettings((current) => ({ ...current, error_rate_threshold: Number(event.target.value) / 100 }))} className={`w-24 accent-[#388bfd] ${!enableEdit ? 'opacity-50 cursor-not-allowed' : ''}`} disabled={!enableEdit} />
            <span className="text-[#7d8590] w-8 text-right" style={{ fontSize: "12px" }}>{Math.round(settings.error_rate_threshold * 100)}%</span>
          </div>
        </Field>
        <Field label="Latency Threshold (P95)" description="P95 latency that triggers a warning">
          <div className="flex items-center gap-2">
            <input aria-label="Latency P95 threshold" type="range" min="100" max="5000" step="100" value={settings.latency_p95_ms} onChange={(event) => setSettings((current) => ({ ...current, latency_p95_ms: Number(event.target.value) }))} className={`w-24 accent-[#388bfd] ${!enableEdit ? 'opacity-50 cursor-not-allowed' : ''}`} disabled={!enableEdit} />
            <span className="text-[#7d8590] w-14 text-right" style={{ fontSize: "12px" }}>{settings.latency_p95_ms}ms</span>
          </div>
        </Field>
        <p className="text-[#7d8590] text-xs py-3">Log retention is an operator-managed database policy and cannot be changed here.</p>
      </Section>

      {/* Save */}
      <FeatureFlag flag="ENABLE_SETTINGS_EDIT">
        <div className="flex justify-end">
          <button type="button" onClick={() => void saveSettings()} disabled={settingsState === "saving" || settingsState === "loading"} className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-[#1f6feb] text-white hover:bg-[#388bfd] transition-colors disabled:opacity-50" style={{ fontSize: "13px", fontWeight: 600 }}>
            <Save className="w-4 h-4" /> Save Changes
          </button>
          <span role="status" className="ml-3 text-xs text-[#7d8590]">{settingsState === "saved" ? "Saved" : settingsState === "error" ? "Save failed" : ""}</span>
        </div>
      </FeatureFlag>
    </div>
  );
}
