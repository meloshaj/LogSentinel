import { Bell, Copy, Eye, EyeOff, Key, RefreshCw, Save, Sliders } from "lucide-react";
import { useState } from "react";
import { FeatureFlag, useFeatureFlag } from "../components/common/FeatureFlag";

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

function Toggle({ defaultOn = false, disabled = false }: { defaultOn?: boolean; disabled?: boolean }) {
  const [on, setOn] = useState(defaultOn);
  return (
    <button
      onClick={() => setOn((v) => !v)}
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
  const [showKey, setShowKey] = useState(false);
  const apiKey = "lsn_test_sk_example_key_1234567890";
  const enableEdit = useFeatureFlag('ENABLE_SETTINGS_EDIT');

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
              {showKey ? apiKey : apiKey.replace(/sk_.+/, `sk_${"*".repeat(20)}`)}
            </code>
            <button onClick={() => setShowKey((v) => !v)} className="text-[#484f58] hover:text-[#7d8590] transition-colors">
              {showKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            </button>
            <button className="text-[#484f58] hover:text-[#7d8590] transition-colors">
              <Copy className="w-3.5 h-3.5" />
            </button>
          </div>
          <FeatureFlag flag="ENABLE_SETTINGS_EDIT">
            <div className="flex gap-2">
              <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors" style={{ fontSize: "11px" }}>
                <RefreshCw className="w-3 h-3" /> Rotate key
              </button>
              <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#1f6feb]/20 text-[#388bfd] hover:bg-[#1f6feb]/30 transition-colors border border-[#1f6feb]/30" style={{ fontSize: "11px" }}>
                <Key className="w-3 h-3" /> Generate new key
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
          <Toggle defaultOn={true} disabled={!enableEdit} />
        </Field>
        <Field label="PagerDuty Integration" description="Route critical incidents to on-call rotation">
          <Toggle defaultOn={true} disabled={!enableEdit} />
        </Field>
        <Field label="Slack Alerts" description="Send alerts to #incidents channel">
          <Toggle defaultOn={true} disabled={!enableEdit} />
        </Field>
        <Field label="Email Digest" description="Daily summary email at 09:00 UTC">
          <Toggle defaultOn={false} disabled={!enableEdit} />
        </Field>
        <Field label="Weekly Report" description="Weekly analytics summary on Mondays">
          <Toggle defaultOn={false} disabled={!enableEdit} />
        </Field>
      </Section>

      {/* Thresholds */}
      <Section title="Detection Thresholds" icon={Sliders}>
        <Field label="Anomaly Score Threshold" description="Score above which an alert is triggered">
          <div className="flex items-center gap-2">
            <input type="range" min="0" max="100" defaultValue="85" className={`w-24 accent-[#388bfd] ${!enableEdit ? 'opacity-50 cursor-not-allowed' : ''}`} disabled={!enableEdit} />
            <span className="text-[#7d8590] w-8 text-right" style={{ fontSize: "12px" }}>0.85</span>
          </div>
        </Field>
        <Field label="Error Rate Threshold" description="Service error rate that triggers warning">
          <div className="flex items-center gap-2">
            <input type="range" min="0" max="100" defaultValue="10" className={`w-24 accent-[#388bfd] ${!enableEdit ? 'opacity-50 cursor-not-allowed' : ''}`} disabled={!enableEdit} />
            <span className="text-[#7d8590] w-8 text-right" style={{ fontSize: "12px" }}>10%</span>
          </div>
        </Field>
        <Field label="Latency Threshold (P95)" description="P95 latency that triggers a warning">
          <div className="flex items-center gap-2">
            <input type="range" min="0" max="100" defaultValue="60" className={`w-24 accent-[#388bfd] ${!enableEdit ? 'opacity-50 cursor-not-allowed' : ''}`} disabled={!enableEdit} />
            <span className="text-[#7d8590] w-8 text-right" style={{ fontSize: "12px" }}>600ms</span>
          </div>
        </Field>
        <Field label="Log Retention" description="How long raw logs are stored">
          <select className={`px-2 py-1 rounded-lg bg-[#0d1117] border border-[#21262d] text-[#7d8590] outline-none ${!enableEdit ? 'opacity-50 cursor-not-allowed' : ''}`} style={{ fontSize: "11px" }} disabled={!enableEdit}>
            <option>7 days</option>
            <option>14 days</option>
            <option selected>30 days</option>
            <option>90 days</option>
          </select>
        </Field>
      </Section>

      {/* Save */}
      <FeatureFlag flag="ENABLE_SETTINGS_EDIT">
        <div className="flex justify-end">
          <button className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-[#1f6feb] text-white hover:bg-[#388bfd] transition-colors" style={{ fontSize: "13px", fontWeight: 600 }}>
            <Save className="w-4 h-4" /> Save Changes
          </button>
        </div>
      </FeatureFlag>
    </div>
  );
}
