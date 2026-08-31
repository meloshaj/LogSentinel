import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { AlertCircle, ArrowLeft, ArrowRight, Mail } from "lucide-react";
import { motion } from "motion/react";
import { InputField, LogSentinelLogo, Spinner, SuccessState } from "./AuthShared";
import { getAuthErrorMessage, setAuthToken } from "../utils/auth";

export function VerifyEmailPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const initialEmail = searchParams.get("email") || "";
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);
  const [success, setSuccess] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const normalized = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized)) {
      setError("Enter a valid email address.");
      return;
    }
    if (!/^\d{6}$/.test(code)) {
      setError("Enter the 6-digit verification code.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const base = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");
      const response = await fetch(`${base}/api/auth/verify-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: normalized, code }),
      });
      if (!response.ok) {
        setError(await getAuthErrorMessage(response, "The verification code is invalid."));
        return;
      }
      const payload = await response.json();
      if (typeof payload.access_token !== "string" || !payload.access_token) {
        setError("The authentication service returned an invalid session.");
        return;
      }
      setAuthToken(payload.access_token);
      setSuccess(true);
      window.setTimeout(() => navigate("/", { replace: true }), 700);
    } catch {
      setError("Unable to connect to the authentication service.");
    } finally {
      setLoading(false);
    }
  };

  const resend = async () => {
    const normalized = email.trim().toLowerCase();
    if (!normalized) {
      setError("Enter your email address first.");
      return;
    }
    setError("");
    setResending(true);
    try {
      const base = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");
      const response = await fetch(`${base}/api/auth/resend-verification`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: normalized }),
      });
      if (!response.ok) {
        setError(await getAuthErrorMessage(response, "Unable to resend the code."));
      }
    } catch {
      setError("Unable to connect to the authentication service.");
    } finally {
      setResending(false);
    }
  };

  return (
    <motion.div
      key="verify-email"
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      className="w-full max-w-[420px] relative z-10"
    >
      <div className="relative bg-white rounded-2xl overflow-hidden border border-slate-700/80 shadow-[0_0_50px_-10px_rgba(14,165,233,0.3)]">
        <div className="bg-[#0b132a] px-6 sm:px-8 py-7 sm:py-8 border-b border-slate-800/80">
          <LogSentinelLogo large dark />
        </div>
        <div className="p-6 sm:p-7">
          <h1 className="text-xl font-bold text-slate-900">Verify your email</h1>
          <p className="text-xs text-slate-600 mt-1 font-medium">
            Enter the 6-digit code sent to your email address.
          </p>
          {error && (
            <div className="mt-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs flex gap-2" role="alert">
              <AlertCircle className="w-4 h-4 shrink-0" aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}
          {success ? (
            <SuccessState title="Email verified" body="Your account is ready. Redirecting you to the dashboard…" />
          ) : (
            <form onSubmit={submit} noValidate className="space-y-3.5 mt-5">
              <InputField
                label="Email address"
                type="email"
                value={email}
                onChange={setEmail}
                icon={<Mail className="w-4 h-4 text-slate-400" />}
                disabled={loading || resending}
              />
              <div>
                <label htmlFor="verification-code" className="block text-xs font-semibold text-slate-700 mb-1.5">Verification code</label>
                <input
                  id="verification-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  pattern="[0-9]{6}"
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  className="w-full px-3 py-2.5 border border-slate-300 rounded-lg text-center tracking-[0.35em] text-lg font-bold focus:outline-none focus:ring-2 focus:ring-sky-500"
                  aria-label="6-digit verification code"
                  disabled={loading}
                />
              </div>
              <button type="submit" disabled={loading} className="w-full py-2.5 rounded-lg bg-sky-500 hover:bg-sky-400 text-white text-sm font-bold flex items-center justify-center gap-2">
                {loading ? <><Spinner /> Verifying…</> : <>Verify email <ArrowRight className="w-4 h-4" /></>}
              </button>
              <button type="button" onClick={resend} disabled={resending || loading} className="w-full text-xs text-sky-600 font-semibold hover:text-sky-700">
                {resending ? "Resending…" : "Resend verification code"}
              </button>
            </form>
          )}
          <div className="mt-4 pt-4 border-t border-slate-100">
            <button onClick={() => navigate("/login")} className="flex items-center gap-2 text-xs text-sky-600 font-semibold mx-auto">
              <ArrowLeft className="w-3.5 h-3.5" /> Back to sign in
            </button>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
