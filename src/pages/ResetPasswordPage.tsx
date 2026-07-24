import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import {
  Lock,
  Eye,
  EyeOff,
  ArrowLeft,
  ArrowRight,
  AlertCircle,
} from "lucide-react";
import { motion } from "motion/react";
import {
  LogSentinelLogo,
  InputField,
  PasswordStrengthBar,
  SuccessState,
  Spinner,
} from "./AuthShared";
import { getAuthErrorMessage } from "../utils/auth";

export function ResetPasswordPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const validate = () => {
    const e: Record<string, string> = {};
    if (!token) e.submit = "Invalid or missing reset token. Please request a new reset link.";
    if (!password) e.password = "Password is required";
    else if (password.length < 8) e.password = "Password must be at least 8 characters";
    if (!confirmPassword) e.confirmPassword = "Please confirm your password";
    else if (password !== confirmPassword) e.confirmPassword = "Passwords do not match";
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
      const response = await fetch(`${apiBase}/api/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: password }),
      });

      if (!response.ok) {
        setErrors({
          submit: await getAuthErrorMessage(response, "Failed to reset password"),
        });
        setLoading(false);
        return;
      }

      setLoading(false);
      setSuccess(true);

      // Redirect to login after showing success
      setTimeout(() => {
        navigate("/login");
      }, 3000);
    } catch {
      setErrors({ submit: "Unable to connect to the server" });
      setLoading(false);
    }
  };

  return (
    <motion.div
      key="reset-password"
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
              Set new password
            </h1>
            <p className="text-sm text-slate-600 mt-1 select-none">
              Choose a strong password for your account
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
              title="Password reset successful"
              body="Your password has been updated. Redirecting you to sign in…"
            />
          ) : (
            <form onSubmit={handleSubmit} noValidate className="space-y-4">
              <div>
                <InputField
                  label="New password"
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
                      className="text-slate-600 hover:text-slate-900 transition-colors cursor-pointer"
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
                <PasswordStrengthBar password={password} />
              </div>

              <InputField
                label="Confirm password"
                type={showConfirm ? "text" : "password"}
                value={confirmPassword}
                onChange={setConfirmPassword}
                placeholder="••••••••"
                icon={<Lock className="w-4 h-4" />}
                error={errors.confirmPassword}
                success={!!confirmPassword && password === confirmPassword}
                rightElement={
                  <button
                    type="button"
                    onClick={() => setShowConfirm((v) => !v)}
                    className="text-slate-600 hover:text-slate-900 transition-colors cursor-pointer"
                    aria-label={showConfirm ? "Hide password" : "Show password"}
                  >
                    {showConfirm ? (
                      <EyeOff className="w-4 h-4" />
                    ) : (
                      <Eye className="w-4 h-4" />
                    )}
                  </button>
                }
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
                    <Spinner /> Resetting password…
                  </>
                ) : (
                  <>
                    Reset Password <ArrowRight className="w-4 h-4" />
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
