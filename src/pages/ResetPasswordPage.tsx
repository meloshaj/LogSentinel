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
      const apiBase = import.meta.env.VITE_API_URL || "";
      const response = await fetch(`${apiBase}/api/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: password, confirm_password: confirmPassword }),
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
        navigate("/login", { replace: true });
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
      <div className="relative bg-white rounded-2xl overflow-hidden border border-slate-700/80 shadow-[0_0_50px_-10px_rgba(14,165,233,0.3)]">
        {/* Header */}
        <div className="bg-[#0b132a] px-6 sm:px-8 py-7 sm:py-8 border-b border-slate-800/80 flex items-center justify-between">
          <LogSentinelLogo large dark />
        </div>

        {/* Content */}
        <div className="p-6 sm:p-7">
          <div className="mb-4 text-left">
            <h1 className="text-xl font-bold text-slate-900 tracking-tight leading-tight select-none">
              Set new password
            </h1>
            <p className="text-xs text-slate-600 mt-1 font-medium select-none">
              Choose a strong password for your account
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
              title="Password reset successful"
              body="Your password has been updated. Redirecting you to sign in…"
            />
          ) : (
            <form onSubmit={handleSubmit} noValidate autoComplete="off" className="space-y-3.5">

              <div>
                <InputField
                  label="New password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={setPassword}
                  placeholder="••••••••"
                  icon={<Lock className="w-4 h-4 text-slate-400" />}
                  error={errors.password}
                  rightElement={
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      className="text-slate-400 hover:text-slate-700 transition-colors cursor-pointer p-0.5"
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
                icon={<Lock className="w-4 h-4 text-slate-400" />}
                error={errors.confirmPassword}
                success={!!confirmPassword && password === confirmPassword}
                rightElement={
                  <button
                    type="button"
                    onClick={() => setShowConfirm((v) => !v)}
                    className="text-slate-400 hover:text-slate-700 transition-colors cursor-pointer p-0.5"
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
