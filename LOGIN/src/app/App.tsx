import { useState } from "react";
import {
  Eye,
  EyeOff,
  Mail,
  Lock,
  User,
  Building2,
  ArrowRight,
  Check,
  AlertCircle,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import logoSrc from "@/imports/1000054290.png";

type Page = "login" | "register";

// ─── Password strength ─────────────────────────────────────────────────────

function getPasswordStrength(password: string): {
  score: number;
  label: string;
  color: string;
} {
  if (!password) return { score: 0, label: "", color: "" };
  let score = 0;
  if (password.length >= 8) score++;
  if (password.length >= 14) score++;
  if (/[A-Z]/.test(password)) score++;
  if (/[0-9]/.test(password)) score++;
  if (/[^A-Za-z0-9]/.test(password)) score++;

  if (score <= 1) return { score: 1, label: "Weak", color: "#ef4444" };
  if (score === 2) return { score: 2, label: "Fair", color: "#f59e0b" };
  if (score === 3) return { score: 3, label: "Good", color: "#3b82f6" };
  return { score: 4, label: "Strong", color: "#10b981" };
}

// ─── Background illustration ───────────────────────────────────────────────

function BackgroundIllustration() {
  const nodes: [number, number][] = [
    [4, 7], [16, 18], [7, 33], [19, 46], [5, 60], [14, 74], [8, 88],
    [28, 5], [33, 20], [24, 38], [36, 53], [27, 68], [32, 84],
    [48, 3], [53, 18], [44, 35], [56, 50], [46, 65], [51, 82], [49, 96],
    [66, 9], [73, 24], [62, 41], [76, 56], [65, 72], [71, 88],
    [83, 6], [90, 20], [84, 37], [93, 52], [87, 68], [91, 84],
    [96, 12], [99, 38], [97, 62], [95, 86],
    [40, 22], [60, 38], [38, 62], [58, 78],
  ];

  const edges: [number, number][] = [];
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const dx = nodes[i][0] - nodes[j][0];
      const dy = nodes[i][1] - nodes[j][1];
      if (Math.sqrt(dx * dx + dy * dy) < 17) {
        edges.push([i, j]);
      }
    }
  }

  const accentNodes: [number, number][] = [
    [16, 18], [73, 24], [36, 53], [87, 68],
  ];

  const streams: [number, number, number, number, number][] = [
    [0, 11, 28, 11, 0],
    [55, 23, 100, 23, 0.5],
    [0, 37, 22, 37, 1.1],
    [70, 49, 100, 49, 0.3],
    [0, 63, 35, 63, 0.8],
    [60, 76, 100, 76, 0.2],
    [0, 89, 18, 89, 1.4],
  ];

  return (
    <>
      <style>{`
        @keyframes nodePulse {
          0%, 100% { opacity: 0.25; }
          50% { opacity: 0.65; }
        }
        @keyframes streamSlide {
          0% { stroke-dashoffset: 12; opacity: 0.06; }
          50% { opacity: 0.18; }
          100% { stroke-dashoffset: 0; opacity: 0.06; }
        }
        @keyframes accentRing {
          0%, 100% { r: 1.2; opacity: 0.06; }
          50% { r: 1.8; opacity: 0.12; }
        }
        .bg-node { animation: nodePulse 3s ease-in-out infinite; }
        .bg-stream { stroke-dasharray: 4 2.5; animation: streamSlide 5s linear infinite; }
        .bg-accent-ring { animation: accentRing 4s ease-in-out infinite; }
      `}</style>
      <svg
        className="fixed inset-0 w-full h-full pointer-events-none select-none"
        viewBox="0 0 100 100"
        preserveAspectRatio="xMidYMid slice"
        aria-hidden="true"
      >
        <defs>
          <radialGradient id="glowCenter" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#0284c7" stopOpacity="0.07" />
            <stop offset="60%" stopColor="#0284c7" stopOpacity="0.025" />
            <stop offset="100%" stopColor="#0284c7" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="glowTopRight" cx="85%" cy="12%" r="35%">
            <stop offset="0%" stopColor="#06b6d4" stopOpacity="0.09" />
            <stop offset="100%" stopColor="#06b6d4" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="glowBottomLeft" cx="12%" cy="88%" r="30%">
            <stop offset="0%" stopColor="#6366f1" stopOpacity="0.07" />
            <stop offset="100%" stopColor="#6366f1" stopOpacity="0" />
          </radialGradient>
        </defs>

        <rect width="100" height="100" fill="url(#glowCenter)" />
        <rect width="100" height="100" fill="url(#glowTopRight)" />
        <rect width="100" height="100" fill="url(#glowBottomLeft)" />

        {/* Log stream lines */}
        {streams.map(([x1, y, x2, _y, delay], i) => (
          <line
            key={`stream-${i}`}
            className="bg-stream"
            x1={x1} y1={y} x2={x2} y2={y}
            stroke="#38bdf8"
            strokeWidth="0.06"
            style={{ animationDelay: `${delay}s` }}
          />
        ))}

        {/* Network edges */}
        {edges.map(([i, j], k) => (
          <line
            key={`edge-${k}`}
            x1={nodes[i][0]} y1={nodes[i][1]}
            x2={nodes[j][0]} y2={nodes[j][1]}
            stroke="#38bdf8"
            strokeWidth="0.07"
            strokeOpacity="0.13"
          />
        ))}

        {/* Accent node halos */}
        {accentNodes.map(([x, y], i) => (
          <circle
            key={`halo-${i}`}
            className="bg-accent-ring"
            cx={x} cy={y} r="1.2"
            fill="none"
            stroke="#0ea5e9"
            strokeWidth="0.08"
            strokeOpacity="0.35"
            style={{ animationDelay: `${i * 1.1}s` }}
          />
        ))}

        {/* Network nodes */}
        {nodes.map(([x, y], i) => (
          <circle
            key={`node-${i}`}
            className="bg-node"
            cx={x} cy={y} r="0.28"
            fill="#38bdf8"
            style={{
              animationDelay: `${(i * 0.41) % 3}s`,
              animationDuration: `${2.4 + (i * 0.11) % 2}s`,
            }}
          />
        ))}

        {/* Accent nodes — brighter */}
        {accentNodes.map(([x, y], i) => (
          <circle
            key={`accent-${i}`}
            cx={x} cy={y} r="0.42"
            fill="#0ea5e9"
            fillOpacity="0.6"
          />
        ))}
      </svg>
    </>
  );
}

// ─── Logo ──────────────────────────────────────────────────────────────────

function LogSentinelLogo({ large = false }: { large?: boolean }) {
  return (
    <div className={`flex items-center ${large ? "gap-3.5" : "gap-2.5"}`}>
      <div className={`flex-shrink-0 rounded-xl overflow-hidden ${large ? "w-24 h-24" : "w-16 h-16"}`} style={{ 
        background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.6) 0%, rgba(30, 41, 59, 0.4) 100%)',
        boxShadow: '0 0 0 1px rgba(148, 163, 184, 0.1), 0 4px 12px rgba(0, 0, 0, 0.3)'
      }}>
        <img
          src={logoSrc}
          alt="LogSentinel logo"
          className="w-full h-full object-cover select-none"
          style={{ 
            filter: 'brightness(1.1) contrast(1.05)',
            mixBlendMode: 'lighten'
          }}
        />
      </div>
      <div>
        <div className={`font-semibold tracking-tight leading-none ${large ? "text-[1.15rem]" : "text-sm"} text-white select-none`}>
          Log<span className="text-sky-400">Sentinel</span>
        </div>
        {large && (
          <p className="text-[10px] font-mono tracking-[0.12em] text-slate-600 mt-1 uppercase select-none">
            Log Intelligence Platform
          </p>
        )}
      </div>
    </div>
  );
}

// ─── Input field ───────────────────────────────────────────────────────────

interface InputFieldProps {
  label: string;
  type: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  icon: React.ReactNode;
  error?: string;
  success?: boolean;
  rightElement?: React.ReactNode;
  optional?: boolean;
}

function InputField({
  label,
  type,
  value,
  onChange,
  placeholder,
  icon,
  error,
  success,
  rightElement,
  optional,
}: InputFieldProps) {
  const borderClass = error
    ? "border-red-400 focus:border-red-500 focus:ring-red-500/20"
    : success
    ? "border-emerald-400 focus:border-emerald-500 focus:ring-emerald-500/20"
    : "border-slate-300 focus:border-sky-500 focus:ring-sky-500/20";

  const prClass = rightElement ? "pr-10" : "pr-3.5";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <label className="text-[13px] font-semibold text-slate-700 leading-none select-none">
          {label}
        </label>
        {optional && (
          <span className="text-[11px] font-mono text-slate-400 tracking-wide select-none">
            OPTIONAL
          </span>
        )}
      </div>
      <div className="relative">
        <div className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
          {icon}
        </div>
        <input
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={[
            "w-full pl-10 py-2.5 bg-white border rounded-lg",
            "text-slate-900 placeholder:text-slate-400 text-sm",
            "transition-all duration-150 focus:outline-none focus:ring-2",
            prClass,
            borderClass,
          ].join(" ")}
        />
        {rightElement && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            {rightElement}
          </div>
        )}
        {success && !rightElement && (
          <div className="absolute right-3.5 top-1/2 -translate-y-1/2">
            <Check className="w-4 h-4 text-emerald-400" />
          </div>
        )}
      </div>
      {error && (
        <p className="flex items-center gap-1.5 text-[12px] text-red-400 select-none">
          <AlertCircle className="w-3 h-3 flex-shrink-0" />
          {error}
        </p>
      )}
    </div>
  );
}

// ─── Password strength bar ─────────────────────────────────────────────────

function PasswordStrengthBar({ password }: { password: string }) {
  if (!password) return null;
  const { score, label, color } = getPasswordStrength(password);
  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex gap-1">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="h-[3px] flex-1 rounded-full transition-all duration-300"
            style={{ backgroundColor: i <= score ? color : "rgba(51,65,85,0.7)" }}
          />
        ))}
      </div>
      <p className="text-[11px] font-mono select-none" style={{ color }}>
        {label}
      </p>
    </div>
  );
}

// ─── Spinner ───────────────────────────────────────────────────────────────

function Spinner() {
  return (
    <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

// ─── Success state ─────────────────────────────────────────────────────────

function SuccessState({ title, body }: { title: string; body: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="flex flex-col items-center justify-center py-10 space-y-3 text-center"
    >
      <div className="w-13 h-13 rounded-full bg-emerald-500/15 border border-emerald-500/35 flex items-center justify-center mb-1">
        <Check className="w-6 h-6 text-emerald-400" strokeWidth={2.5} />
      </div>
      <p className="text-slate-900 font-semibold select-none">{title}</p>
      <p className="text-sm text-slate-600 max-w-[240px] select-none">{body}</p>
    </motion.div>
  );
}

// ─── SSO icons ────────────────────────────────────────────────────────────

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path d="M17.64 9.205c0-.639-.057-1.252-.164-1.841H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z" fill="#4285F4" />
      <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z" fill="#34A853" />
      <path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332Z" fill="#FBBC05" />
      <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58Z" fill="#EA4335" />
    </svg>
  );
}

function MicrosoftIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <rect x="0" y="0" width="8.5" height="8.5" fill="#F25022" />
      <rect x="9.5" y="0" width="8.5" height="8.5" fill="#7FBA00" />
      <rect x="0" y="9.5" width="8.5" height="8.5" fill="#00A4EF" />
      <rect x="9.5" y="9.5" width="8.5" height="8.5" fill="#FFB900" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z" />
    </svg>
  );
}

// ─── SSO buttons ───────────────────────────────────────────────────────────

interface SSOProvider {
  id: string;
  label: string;
  icon: React.ReactNode;
  onLogin: () => void;
}

function SSOButton({ provider }: { provider: SSOProvider }) {
  return (
    <button
      type="button"
      onClick={provider.onLogin}
      aria-label={`Continue with ${provider.id}`}
      className={[
        "w-full flex items-center gap-3 px-4 py-2.5 rounded-[11px]",
        "bg-slate-50 border-2 border-slate-300",
        "text-[13px] font-medium text-slate-700",
        "transition-all duration-150",
        "hover:bg-slate-100 hover:border-slate-400 hover:text-slate-900",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2",
        "active:scale-[0.985]",
        "select-none",
      ].join(" ")}
    >
      <span className="flex-shrink-0 w-[18px] flex items-center justify-center">
        {provider.icon}
      </span>
      <span className="flex-1 text-center">{provider.label}</span>
    </button>
  );
}

function SSOSection({
  onGoogle,
  onMicrosoft,
  onGitHub,
}: {
  onGoogle: () => void;
  onMicrosoft: () => void;
  onGitHub: () => void;
}) {
  const providers: SSOProvider[] = [
    { id: "Google",    label: "Continue with Google",    icon: <GoogleIcon />,    onLogin: onGoogle },
    { id: "Microsoft", label: "Continue with Microsoft", icon: <MicrosoftIcon />, onLogin: onMicrosoft },
    { id: "GitHub",    label: "Continue with GitHub",    icon: <GitHubIcon />, onLogin: onGitHub },
  ];

  return (
    <div className="mt-5">
      {/* Divider */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1 h-px bg-slate-300" />
        <span className="text-[11px] font-semibold tracking-[0.08em] text-slate-700 uppercase select-none">
          or continue with
        </span>
        <div className="flex-1 h-px bg-slate-300" />
      </div>

      {/* Provider buttons */}
      <div className="space-y-2.5">
        {providers.map((p) => (
          <SSOButton key={p.id} provider={p} />
        ))}
      </div>
    </div>
  );
}

// ─── Login page ────────────────────────────────────────────────────────────

function LoginPage({ onNavigate }: { onNavigate: (p: Page) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const validate = () => {
    const e: Record<string, string> = {};
    if (!email) e.email = "Email address is required";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) e.email = "Enter a valid email address";
    if (!password) e.password = "Password is required";
    else if (password.length < 8) e.password = "Password must be at least 8 characters";
    return e;
  };

  const handleSubmit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    const errs = validate();
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setLoading(true);
    await new Promise((r) => setTimeout(r, 1300));
    setLoading(false);
    setSuccess(true);
  };

  const handleGoogleSSO = () => console.log("SSO: Google sign-in initiated");
  const handleMicrosoftSSO = () => console.log("SSO: Microsoft sign-in initiated");
  const handleGitHubSSO = () => console.log("SSO: GitHub sign-in initiated");

  return (
    <motion.div
      key="login"
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -14 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
      className="w-full max-w-[420px]"
    >
      <div className="relative bg-white rounded-2xl overflow-hidden shadow-[0_24px_64px_rgba(0,0,0,0.3)] border-[3px] border-sky-600">
        {/* Darker header section for logo */}
        <div className="bg-gradient-to-br from-slate-800 to-slate-900 px-8 pt-8 pb-6">
          <LogSentinelLogo large />
        </div>
        
        {/* White form content */}
        <div className="p-8">

        <div className="mb-6">
          <h1 className="text-[1.4rem] font-semibold text-slate-900 tracking-tight leading-tight select-none">
            Welcome back
          </h1>
          <p className="text-sm text-slate-600 mt-1 select-none">
            Sign in to your workspace to continue
          </p>
        </div>

        {success ? (
          <SuccessState
            title="Signed in successfully"
            body="Redirecting you to your dashboard..."
          />
        ) : (
          <form onSubmit={handleSubmit} noValidate className="space-y-4">
            <InputField
              label="Email address"
              type="email"
              value={email}
              onChange={setEmail}
              placeholder="you@company.com"
              icon={<Mail className="w-4 h-4" />}
              error={errors.email}
            />

            <InputField
              label="Password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={setPassword}
              placeholder="••••••••"
              icon={<Lock className="w-4 h-4" />}
              error={errors.password}
              rightElement={
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="text-slate-600 hover:text-slate-900 transition-colors"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? (
                    <EyeOff className="w-4 h-4" />
                  ) : (
                    <Eye className="w-4 h-4" />
                  )}
                </button>
              }
            />

            {/* Remember me + forgot */}
            <div className="flex items-center justify-between pt-0.5">
              <label className="flex items-center gap-2.5 cursor-pointer group">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="sr-only"
                />
                <div
                  className={[
                    "w-4 h-4 rounded border-2 flex items-center justify-center transition-all duration-150 flex-shrink-0",
                    rememberMe
                      ? "bg-sky-500 border-sky-500"
                      : "border-slate-400 bg-white group-hover:border-slate-600",
                  ].join(" ")}
                >
                  {rememberMe && (
                    <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />
                  )}
                </div>
                <span className="text-[13px] text-slate-700 font-medium select-none">
                  Remember me
                </span>
              </label>
              <button
                type="button"
                className="text-[13px] text-sky-600 hover:text-sky-700 font-medium transition-colors select-none"
              >
                Forgot password?
              </button>
            </div>

            <button
              type="submit"
              disabled={loading}
              className={[
                "w-full py-2.5 rounded-lg text-sm font-semibold",
                "flex items-center justify-center gap-2 mt-1",
                "transition-all duration-150 select-none",
                loading
                  ? "bg-sky-600/40 text-sky-400/70 cursor-not-allowed"
                  : "bg-sky-500 hover:bg-sky-400 text-white shadow-lg shadow-sky-500/25 hover:shadow-sky-500/35 active:scale-[0.99]",
              ].join(" ")}
            >
              {loading ? (
                <>
                  <Spinner /> Signing in…
                </>
              ) : (
                <>
                  Sign In <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>
        )}

        {!success && (
          <SSOSection
            onGoogle={handleGoogleSSO}
            onMicrosoft={handleMicrosoftSSO}
            onGitHub={handleGitHubSSO}
          />
        )}

        <div className="mt-6 pt-5 border-t border-slate-200">
          <p className="text-[13px] text-center text-slate-600 select-none">
            No account yet?{" "}
            <button
              onClick={() => onNavigate("register")}
              className="text-sky-600 hover:text-sky-700 font-medium transition-colors select-none"
            >
              Create a free workspace
            </button>
          </p>
        </div>
        </div>
      </div>
    </motion.div>
  );
}

// ─── Register page ─────────────────────────────────────────────────────────

function RegisterPage({ onNavigate }: { onNavigate: (p: Page) => void }) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [org, setOrg] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const validate = () => {
    const e: Record<string, string> = {};
    if (!fullName.trim()) e.fullName = "Full name is required";
    if (!email) e.email = "Work email is required";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) e.email = "Enter a valid email address";
    if (!password) e.password = "Password is required";
    else if (password.length < 8) e.password = "At least 8 characters required";
    if (!confirmPw) e.confirmPw = "Please confirm your password";
    else if (password !== confirmPw) e.confirmPw = "Passwords do not match";
    if (!agreed) e.agreed = "You must accept the terms to continue";
    return e;
  };

  const handleSubmit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    const errs = validate();
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setLoading(true);
    await new Promise((r) => setTimeout(r, 1500));
    setLoading(false);
    setSuccess(true);
  };

  const handleGoogleSSO = () => console.log("SSO: Google register initiated");
  const handleMicrosoftSSO = () => console.log("SSO: Microsoft register initiated");
  const handleGitHubSSO = () => console.log("SSO: GitHub register initiated");

  const pwMatch =
    password.length > 0 && confirmPw.length > 0 && password === confirmPw;

  return (
    <motion.div
      key="register"
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -14 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
      className="w-full max-w-[460px]"
    >
      <div className="relative bg-white rounded-2xl overflow-hidden shadow-[0_24px_64px_rgba(0,0,0,0.3)] border-[3px] border-sky-600">
        {/* Darker header section for logo */}
        <div className="bg-gradient-to-br from-slate-800 to-slate-900 px-8 pt-8 pb-6">
          <LogSentinelLogo large />
        </div>
        
        {/* White form content */}
        <div className="p-8">

        <div className="mb-6">
          <h1 className="text-[1.4rem] font-semibold text-slate-900 tracking-tight leading-tight select-none">
            Create account
          </h1>
          <p className="text-sm text-slate-600 mt-1 select-none">
            Start monitoring your infrastructure in minutes
          </p>
        </div>

        {success ? (
          <SuccessState
            title="Account created!"
            body="Check your inbox — we've sent a verification link to confirm your email."
          />
        ) : (
          <form onSubmit={handleSubmit} noValidate className="space-y-3">
            {/* Row 1: Name + Email */}
            <div className="grid grid-cols-2 gap-2.5">
              <InputField
                label="Full name"
                type="text"
                value={fullName}
                onChange={setFullName}
                placeholder="Alex Chen"
                icon={<User className="w-4 h-4" />}
                error={errors.fullName}
              />
              <InputField
                label="Work email"
                type="email"
                value={email}
                onChange={setEmail}
                placeholder="you@company.com"
                icon={<Mail className="w-4 h-4" />}
                error={errors.email}
              />
            </div>

            <InputField
              label="Organization"
              type="text"
              value={org}
              onChange={setOrg}
              placeholder="Acme Corp"
              icon={<Building2 className="w-4 h-4" />}
              optional
            />

            {/* Row 2: Password + Confirm */}
            <div className="grid grid-cols-2 gap-2.5">
              <div>
                <InputField
                  label="Password"
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={setPassword}
                  placeholder="Min. 8 chars"
                  icon={<Lock className="w-4 h-4" />}
                  error={errors.password}
                  rightElement={
                    <button
                      type="button"
                      onClick={() => setShowPw((v) => !v)}
                      className="text-slate-600 hover:text-slate-900 transition-colors"
                      aria-label={showPw ? "Hide password" : "Show password"}
                    >
                      {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  }
                />
                <PasswordStrengthBar password={password} />
              </div>
              <InputField
                label="Confirm password"
                type={showConfirm ? "text" : "password"}
                value={confirmPw}
                onChange={setConfirmPw}
                placeholder="Repeat password"
                icon={<Lock className="w-4 h-4" />}
                error={errors.confirmPw}
                success={pwMatch}
                rightElement={
                  <button
                    type="button"
                    onClick={() => setShowConfirm((v) => !v)}
                    className="text-slate-600 hover:text-slate-900 transition-colors"
                    aria-label={showConfirm ? "Hide password" : "Show password"}
                  >
                    {showConfirm ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                }
              />
            </div>

            {/* Terms */}
            <div className="pt-0.5">
              <label className="flex items-start gap-2.5 cursor-pointer group">
                <input
                  type="checkbox"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  className="sr-only"
                />
                <div
                  className={[
                    "mt-0.5 w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 transition-all duration-150",
                    agreed
                      ? "bg-sky-500 border-sky-500"
                      : errors.agreed
                      ? "border-red-400 bg-white"
                      : "border-slate-400 bg-white group-hover:border-slate-600",
                  ].join(" ")}
                >
                  {agreed && (
                    <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />
                  )}
                </div>
                <span className="text-[13px] text-slate-700 leading-relaxed select-none">
                  I agree to the{" "}
                  <button
                    type="button"
                    className="text-sky-600 hover:text-sky-700 font-medium transition-colors underline-offset-2 hover:underline select-none"
                  >
                    Terms of Service
                  </button>{" "}
                  and{" "}
                  <button
                    type="button"
                    className="text-sky-600 hover:text-sky-700 font-medium transition-colors underline-offset-2 hover:underline select-none"
                  >
                    Privacy Policy
                  </button>
                </span>
              </label>
              {errors.agreed && (
                <p className="flex items-center gap-1.5 text-[12px] text-red-400 mt-1.5 ml-[26px] select-none">
                  <AlertCircle className="w-3 h-3 flex-shrink-0" />
                  {errors.agreed}
                </p>
              )}
            </div>

            <button
              type="submit"
              disabled={loading}
              className={[
                "w-full py-2.5 rounded-lg text-sm font-semibold",
                "flex items-center justify-center gap-2 mt-1",
                "transition-all duration-150 select-none",
                loading
                  ? "bg-sky-600/40 text-sky-400/70 cursor-not-allowed"
                  : "bg-sky-500 hover:bg-sky-400 text-white shadow-lg shadow-sky-500/25 hover:shadow-sky-500/35 active:scale-[0.99]",
              ].join(" ")}
            >
              {loading ? (
                <>
                  <Spinner /> Creating account…
                </>
              ) : (
                <>
                  Create Account <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>
        )}

        {!success && (
          <SSOSection
            onGoogle={handleGoogleSSO}
            onMicrosoft={handleMicrosoftSSO}
            onGitHub={handleGitHubSSO}
          />
        )}

        <div className="mt-5 pt-5 border-t border-slate-200">
          <p className="text-[13px] text-center text-slate-600 select-none">
            Already have an account?{" "}
            <button
              onClick={() => onNavigate("login")}
              className="text-sky-600 hover:text-sky-700 font-medium transition-colors select-none"
            >
              Sign in
            </button>
          </p>
        </div>
        </div>
      </div>
    </motion.div>
  );
}

// ─── App root ──────────────────────────────────────────────────────────────

export default function App() {
  const [page, setPage] = useState<Page>("login");

  return (
    <div className="relative min-h-screen w-full flex flex-col items-center justify-center overflow-hidden px-4 py-12 font-[Inter,system-ui,sans-serif]">
      {/* Base canvas */}
      <div className="fixed inset-0 bg-[#060c18]" />

      {/* Layered gradients */}
      <div className="fixed inset-0 bg-gradient-to-br from-blue-950/20 via-transparent to-indigo-950/15" />
      <div className="fixed inset-0 bg-[radial-gradient(ellipse_80%_60%_at_50%_50%,rgba(14,165,233,0.05)_0%,transparent_70%)]" />

      {/* Network illustration */}
      <BackgroundIllustration />

      {/* Page content */}
      <div className="relative z-10 w-full flex flex-col items-center gap-6">
        {/* Tagline pill */}
        <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-slate-700/40 bg-slate-900/50 backdrop-blur-sm">
          <div className="w-1.5 h-1.5 rounded-full bg-sky-400 animate-pulse" />
          <span className="text-[11px] font-mono tracking-[0.14em] text-slate-500 uppercase select-none">
            Real-Time Log Intelligence &amp; Root Cause Analysis
          </span>
        </div>

        {/* Auth card with animated page transitions */}
        <AnimatePresence mode="wait">
          {page === "login" ? (
            <LoginPage key="login" onNavigate={setPage} />
          ) : (
            <RegisterPage key="register" onNavigate={setPage} />
          )}
        </AnimatePresence>

        {/* Footer trust signals */}
        <div className="flex items-center gap-4 text-[11px] text-slate-700 font-mono select-none">
          <span>© 2025 LogSentinel Inc.</span>
          <span className="w-px h-3 bg-slate-800" />
          <span>SOC 2 Type II</span>
          <span className="w-px h-3 bg-slate-800" />
          <span>99.99% SLA</span>
          <span className="w-px h-3 bg-slate-800" />
          <span>GDPR</span>
        </div>
      </div>
    </div>
  );
}
