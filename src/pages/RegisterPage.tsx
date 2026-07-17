import { useState } from "react";
import { useNavigate } from "react-router";
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
import { motion } from "motion/react";
import {
  LogSentinelLogo,
  InputField,
  PasswordStrengthBar,
  SSOSection,
  SuccessState,
  Spinner,
} from "./AuthShared";
import { getAuthErrorMessage, setAuthToken } from "../utils/auth";

export function RegisterPage() {
  const navigate = useNavigate();
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
    try {
      const apiBase = import.meta.env.VITE_API_URL || "http://localhost:8000";
      const response = await fetch(`${apiBase}/api/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          fullName,
          organization: org,
        }),
      });

      if (!response.ok) {
        setErrors({
          submit: await getAuthErrorMessage(response, "Registration failed"),
        });
        setLoading(false);
        return;
      }

      // Auto-authenticate user after successful registration
      const loginResponse = await fetch(`${apiBase}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!loginResponse.ok) {
        setErrors({
          submit: await getAuthErrorMessage(
            loginResponse,
            "Account created, but automatic sign-in failed. Please sign in.",
          ),
        });
        setLoading(false);
        return;
      }

      const loginData = await loginResponse.json();
      if (typeof loginData.access_token !== "string" || !loginData.access_token) {
        setErrors({
          submit: "Account created, but the authentication server returned an invalid token. Please sign in.",
        });
        setLoading(false);
        return;
      }

      setAuthToken(loginData.access_token);

      setLoading(false);
      setSuccess(true);

      // Redirect to dashboard after a short delay showing the success state
      setTimeout(() => {
        navigate("/");
      }, 1000);
    } catch (err) {
      setErrors({ submit: "Unable to connect to authentication server" });
      setLoading(false);
    }
  };

  const handleSSO = () => {
    setErrors({
      submit: "Single sign-on is not connected yet. Create an account with email and password.",
    });
  };

  const pwMatch =
    password.length > 0 && confirmPw.length > 0 && password === confirmPw;

  return (
    <motion.div
      key="register"
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -14 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
      className="w-full max-w-[460px] relative z-10"
    >
      <div className="relative bg-white rounded-2xl overflow-hidden shadow-[0_24px_64px_rgba(0,0,0,0.3)] border-[3px] border-sky-600">
        {/* Darker header section for logo */}
        <div className="bg-gradient-to-br from-slate-800 to-slate-900 px-8 pt-8 pb-6">
          <LogSentinelLogo large />
        </div>
        
        {/* White form content */}
        <div className="p-8">
            <div className="mb-6 text-left">
              <h1 className="text-[1.4rem] font-semibold text-slate-900 tracking-tight leading-tight select-none">
                Create account
              </h1>
              <p className="text-sm text-slate-600 mt-1 select-none">
                Start monitoring your infrastructure in minutes
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
                title="Account created!"
                body="Your workspace is ready. Redirecting you to the dashboard..."
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
                  <div className="text-left">
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
                          className="text-slate-600 hover:text-slate-900 transition-colors cursor-pointer"
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
                        className="text-slate-600 hover:text-slate-900 transition-colors cursor-pointer"
                        aria-label={showConfirm ? "Hide password" : "Show password"}
                      >
                        {showConfirm ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </button>
                    }
                  />
                </div>

                {/* Terms */}
                <div className="pt-0.5 text-left">
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
                        className="text-sky-600 hover:text-sky-700 font-medium transition-colors underline-offset-2 hover:underline select-none cursor-pointer"
                      >
                        Terms of Service
                      </button>{" "}
                      and{" "}
                      <button
                        type="button"
                        className="text-sky-600 hover:text-sky-700 font-medium transition-colors underline-offset-2 hover:underline select-none cursor-pointer"
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
                    "transition-all duration-150 select-none cursor-pointer",
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
                onGoogle={handleSSO}
                onMicrosoft={handleSSO}
                onGitHub={handleSSO}
              />
            )}

            <div className="mt-5 pt-5 border-t border-slate-200">
              <p className="text-[13px] text-center text-slate-600 select-none">
                Already have an account?{" "}
                <button
                  onClick={() => navigate("/login")}
                  className="text-sky-600 hover:text-sky-700 font-medium transition-colors select-none cursor-pointer"
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
