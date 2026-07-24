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
      className="w-full max-w-[420px] relative z-10"
    >
      <div className="relative bg-white rounded-2xl overflow-hidden shadow-[0_24px_64px_rgba(0,0,0,0.3)] border-[3px] border-sky-600">
        {/* Header */}
        <div className="bg-gradient-to-br from-slate-800 to-slate-900 px-8 pt-8 pb-6">
          <LogSentinelLogo large />
        </div>

        {/* Content */}
        <div className="p-8">
          <div className="mb-6 text-left">
            <h1 className="text-[1.4rem] font-semibold text-slate-900 tracking-tight leading-tight select-none">
              Reset your password
            </h1>
            <p className="text-sm text-slate-600 mt-1 select-none">
              Enter your email address and we'll send you a link to reset your
              password
            </p>
          </div>

          {errors.submit && (
            <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-500 text-xs flex items-center gap-2 select-none text-left">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{errors.submit}</span>
            </div>
          )}

          {success ? (
            <SuccessState
              title="Check your email"
              body="If an account exists with that email, we've sent a password reset link. Check your inbox (and spam folder)."
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

              <button
                type="submit"
                disabled={loading}
                className={[
                  "w-full py-2.5 rounded-lg text-sm font-semibold",
                  "flex items-center justify-center gap-2 mt-1",
                  "transition-all duration-150 select-none cursor-pointer",
                  loading
                    ? "bg-sky-600/40 text-sky-400/70 cursor-not-allowed"
                    : "bg-sky-500 hover:bg-sky-400 text-white shadow-lg shadow-sky-500/25 hover:shadow-sky-500/35 active:scale-[0.99]",
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

          <div className="mt-6 pt-5 border-t border-slate-200">
            <button
              onClick={() => navigate("/login")}
              className="flex items-center gap-2 text-[13px] text-sky-600 hover:text-sky-700 font-medium transition-colors select-none cursor-pointer mx-auto"
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
