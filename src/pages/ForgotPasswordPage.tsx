import { useState } from "react";
import { useNavigate } from "react-router";
import { Mail, ArrowLeft, ArrowRight, AlertCircle } from "lucide-react";
import { motion } from "motion/react";
import {
  LogSentinelLogo,
  InputField,
  SuccessState,
  Spinner,
} from "./AuthShared";
import { getAuthErrorMessage } from "../utils/auth";

export function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const validate = () => {
    const e: Record<string, string> = {};
    if (!email) e.email = "Email address is required";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
      e.email = "Enter a valid email address";
    return e;
  };

  const handleSubmit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    const errs = validate();
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setLoading(true);

    try {
      const apiBase = import.meta.env.VITE_API_URL || "http://localhost:8000";
      const response = await fetch(`${apiBase}/api/auth/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });

      if (!response.ok) {
        setErrors({
          submit: await getAuthErrorMessage(response, "Something went wrong"),
        });
        setLoading(false);
        return;
      }

      setLoading(false);
      setSuccess(true);
    } catch {
      setErrors({ submit: "Unable to connect to the server" });
      setLoading(false);
    }
  };

  return (
    <motion.div
      key="forgot-password"
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -14 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
      className="w-full max-w-[420px] relative z-10">
      <div className="relative bg-white rounded-2xl overflow-hidden border border-slate-700/80 shadow-[0_0_50px_-10px_rgba(14,165,233,0.3)]">
        {/* Header */}
        <div className="bg-[#0b132a] px-6 sm:px-8 py-7 sm:py-8 border-b border-slate-800/80 flex items-center justify-between">
          <LogSentinelLogo large dark />
        </div>

        {/* Content */}
        <div className="p-6 sm:p-7">
          <div className="mb-4 text-left">
            <h1 className="text-xl font-bold text-slate-900 tracking-tight leading-tight select-none">
              Reset your password
            </h1>
            <p className="text-xs text-slate-600 mt-1 font-medium select-none">
              Enter your email address and we'll send you a link to reset your
              password
            </p>
          </div>

          {errors.submit && (
            <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs flex items-center gap-2 select-none text-left shadow-sm">
              <AlertCircle className="w-4 h-4 shrink-0 text-red-600" />
              <span className="font-medium">{errors.submit}</span>
            </div>
          )}

          {success ? (
            <SuccessState
              title="Check your email"
              body="If an account exists with that email, we've sent a password reset link. Check your inbox (and spam folder)."
            />
          ) : (
            <form onSubmit={handleSubmit} noValidate autoComplete="off" className="space-y-3.5">

              <InputField
                label="Email address"
                type="email"
                value={email}
                onChange={setEmail}
                placeholder="you@company.com"
                icon={<Mail className="w-4 h-4 text-slate-400" />}
                error={errors.email}
              />

              <button
                type="submit"
                disabled={loading}
                className={[
                  "w-full py-2.5 rounded-lg text-sm font-bold",
                  "flex items-center justify-center gap-2 mt-2",
                  "transition-all duration-150 select-none cursor-pointer shadow-md",
                  loading
                    ? "bg-sky-100 text-sky-400 cursor-not-allowed shadow-none"
                    : "bg-sky-500 hover:bg-sky-400 text-white shadow-sky-500/20 active:scale-[0.99]",
                ].join(" ")}
              >
                {loading ? (
                  <>
                    <Spinner /> Sending reset link…
                  </>
                ) : (
                  <>
                    Send Reset Link <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </form>
          )}

          <div className="mt-4 pt-4 border-t border-slate-100">
            <button
              onClick={() => navigate("/login")}
              className="flex items-center gap-2 text-xs text-sky-600 hover:text-sky-700 font-semibold transition-colors select-none cursor-pointer mx-auto"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              Back to sign in
            </button>
          </div>
        </div>
      </div>
    </motion.div>

  );
}
