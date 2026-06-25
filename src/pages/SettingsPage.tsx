import { Bell, Copy, Eye, EyeOff, Key, RefreshCw, Save, Sliders, Users } from "lucide-react";
import { useState } from "react";

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

function Toggle({ defaultOn = false }: { defaultOn?: boolean }) {
  const [on, setOn] = useState(defaultOn);
  return (
    <button
      onClick={() => setOn((v) => !v)}
      className="relative w-10 h-5 rounded-full transition-colors"
      style={{ background: on ? "#1f6feb" : "#21262d" }}
    >
      <span
        className="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform"
        style={{ transform: on ? "translateX(20px)" : "translateX(0)" }}
      />
    </button>
  );
}

const TEAM_MEMBERS = [
  { name: "Alex Chen",      role: "Admin",    avatar: "AC", status: "online" },
  { name: "Jordan Smith",   role: "Engineer", avatar: "JS", status: "online" },
  { name: "Taylor Rivera",  role: "Viewer",   avatar: "TR", status: "offline" },
];

export function SettingsPage() {
  const [showKey, setShowKey] = useState(false);
  const apiKey = "lsn_prod_sk_a1b2c3d4e5f6g7h8i9j0";

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
          <div className="flex gap-2">
            <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors" style={{ fontSize: "11px" }}>
              <RefreshCw className="w-3 h-3" /> Rotate key
            </button>
            <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#1f6feb]/20 text-[#388bfd] hover:bg-[#1f6feb]/30 transition-colors border border-[#1f6feb]/30" style={{ fontSize: "11px" }}>
              <Key className="w-3 h-3" /> Generate new key
            </button>
          </div>
          <div className="text-[#484f58]" style={{ fontSize: "10px" }}>
            Use this key to authenticate LogSentinel API requests. Keep it secret - it has full access to your workspace.
          </div>
        </div>
      </Section>

      {/* Notifications */}
      <Section title="Notification Settings" icon={Bell}>
        <Field label="Critical Alerts" description="Get notified when anomaly score exceeds 0.85">
          <Toggle defaultOn={true} />
        </Field>
        <Field label="PagerDuty Integration" description="Route critical incidents to on-call rotation">
          <Toggle defaultOn={true} />
        </Field>
        <Field label="Slack Alerts" description="Send alerts to #incidents channel">
          <Toggle defaultOn={true} />
        </Field>
        <Field label="Email Digest" description="Daily summary email at 09:00 UTC">
          <Toggle defaultOn={false} />
        </Field>
        <Field label="Weekly Report" description="Weekly analytics summary on Mondays">
          <Toggle defaultOn={false} />
        </Field>
      </Section>

      {/* Thresholds */}
      <Section title="Detection Thresholds" icon={Sliders}>
        <Field label="Anomaly Score Threshold" description="Score above which an alert is triggered">
          <div className="flex items-center gap-2">
            <input type="range" min="0" max="100" defaultValue="85" className="w-24 accent-[#388bfd]" />
            <span className="text-[#7d8590] w-8 text-right" style={{ fontSize: "12px" }}>0.85</span>
          </div>
        </Field>
        <Field label="Error Rate Threshold" description="Service error rate that triggers warning">
          <div className="flex items-center gap-2">
            <input type="range" min="0" max="100" defaultValue="10" className="w-24 accent-[#388bfd]" />
            <span className="text-[#7d8590] w-8 text-right" style={{ fontSize: "12px" }}>10%</span>
          </div>
        </Field>
        <Field label="Latency Threshold (P95)" description="P95 latency that triggers a warning">
          <div className="flex items-center gap-2">
            <input type="range" min="0" max="100" defaultValue="60" className="w-24 accent-[#388bfd]" />
            <span className="text-[#7d8590] w-8 text-right" style={{ fontSize: "12px" }}>600ms</span>
          </div>
        </Field>
        <Field label="Log Retention" description="How long raw logs are stored">
          <select className="px-2 py-1 rounded-lg bg-[#0d1117] border border-[#21262d] text-[#7d8590] outline-none" style={{ fontSize: "11px" }}>
            <option>7 days</option>
            <option>14 days</option>
            <option selected>30 days</option>
            <option>90 days</option>
          </select>
        </Field>
      </Section>

      {/* Team */}
      <Section title="Team Management" icon={Users}>
        <div className="space-y-2">
          {TEAM_MEMBERS.map((m) => (
            <div key={m.name} className="flex items-center gap-3 p-3 rounded-lg bg-[#0d1117] border border-[#21262d]">
              <div className="flex items-center justify-center w-8 h-8 rounded-full bg-gradient-to-br from-[#1f6feb] to-[#388bfd] shrink-0">
                <span className="text-white" style={{ fontSize: "11px", fontWeight: 700 }}>{m.avatar}</span>
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 500 }}>{m.name}</span>
                  <span className={`w-1.5 h-1.5 rounded-full ${m.status === "online" ? "bg-[#3fb950]" : "bg-[#484f58]"}`} />
                </div>
                <span className="text-[#484f58]" style={{ fontSize: "10px" }}>{m.role}</span>
              </div>
              <select className="px-2 py-1 rounded-lg bg-[#161b22] border border-[#21262d] text-[#7d8590] outline-none" style={{ fontSize: "11px" }}>
                <option>Admin</option>
                <option>Engineer</option>
                <option>Viewer</option>
              </select>
            </div>
          ))}
          <button className="w-full py-2 rounded-lg border border-dashed border-[#21262d] text-[#484f58] hover:text-[#7d8590] hover:border-[#484f58] transition-colors" style={{ fontSize: "11px" }}>
            + Invite team member
          </button>
        </div>
      </Section>

      {/* Save */}
      <div className="flex justify-end">
        <button className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-[#1f6feb] text-white hover:bg-[#388bfd] transition-colors" style={{ fontSize: "13px", fontWeight: 600 }}>
          <Save className="w-4 h-4" /> Save Changes
        </button>
      </div>
    </div>
  );
}
