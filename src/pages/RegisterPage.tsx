import { useCallback, useState } from "react";
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
import { useGoogleLogin } from "@react-oauth/google";
import {
  GoogleIcon,
  GitHubIcon,
  InputField,
  LogSentinelLogo,
  MicrosoftIcon,
  PasswordStrengthBar,
  SSOButton,
  Spinner,
  SuccessState,
} from "./AuthShared";
import { useMicrosoftAuth } from "../hooks/useMicrosoftAuth";
import {
  useMicrosoftAuthStatus,
  type MicrosoftAuthStatus,
} from "../providers/MsalProviderWrapper";
import { getAuthErrorMessage, setAuthToken } from "../utils/auth";
import { FeatureFlag } from "../components/common/FeatureFlag";

function ConfiguredMicrosoftRegisterButton({
  disabled,
  onSuccess,
  onError,
}: {
  disabled: boolean;
  onSuccess: () => void;
  onError: (message: string) => void;
}) {
  const microsoftAuth = useMicrosoftAuth();

  const handleLogin = async () => {
    try {
      const result = await microsoftAuth.login(false);
      if (result.success) {
        onSuccess();
      } else if (result.error) {
        onError(result.error);
      }
    } catch {
      onError("Microsoft sign-up could not be completed. Please try again.");
    }
  };

  return (
    <SSOButton
      provider={{
        id: "Microsoft",
        label: microsoftAuth.loading
          ? "Signing in with Microsoft…"
          : "Continue with Microsoft",
        icon: <MicrosoftIcon />,
        onLogin: handleLogin,
        disabled: disabled || microsoftAuth.loading,
        loading: microsoftAuth.loading,
      }}
    />
  );
}

function UnavailableMicrosoftRegisterButton({
  status,
}: {
  status: Exclude<MicrosoftAuthStatus, "ready">;
}) {
  const message =
    status === "error"
      ? "Microsoft sign-in is temporarily unavailable."
      : "Microsoft sign-in is not configured.";

  return (
    <div className="space-y-1">
      <SSOButton
        provider={{
          id: "Microsoft",
          label: "Continue with Microsoft",
          icon: <MicrosoftIcon />,
          onLogin: () => undefined,
          disabled: true,
          descriptionId: "microsoft-register-availability",
          title: message,
        }}
      />
      <p
        id="microsoft-register-availability"
        className="px-1 text-[11px] text-slate-400"
      >
        {message}
      </p>
    </div>
  );
}

function MicrosoftRegisterEntry(props: {
  disabled: boolean;
  onSuccess: () => void;
  onError: (message: string) => void;
}) {
  const status = useMicrosoftAuthStatus();

  if (status === "ready") {
    return <ConfiguredMicrosoftRegisterButton {...props} />;
  }

  return <UnavailableMicrosoftRegisterButton status={status} />;
}

export function RegisterPage() {
  const navigate = useNavigate();
  const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";
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
      const apiBase = (
        import.meta.env.VITE_API_URL || ""
      ).replace(/\/+$/, "");
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
        if (response.status === 409) {
          setErrors({
            conflict: await getAuthErrorMessage(response, "Account conflict"),
          });
        } else {
          setErrors({
            submit: await getAuthErrorMessage(response, "Registration failed"),
          });
        }
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

      setTimeout(() => {
        navigate("/");
      }, 1000);
    } catch {
      setErrors({ submit: "Unable to connect to authentication server" });
      setLoading(false);
    }
  };

  const googleLogin = useGoogleLogin({
    onSuccess: async (tokenResponse) => {
      setLoading(true);
      setErrors({});

      try {
        const apiBase = (
          import.meta.env.VITE_API_URL || ""
        ).replace(/\/+$/, "");
        const response = await fetch(`${apiBase}/api/auth/google`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ credential: tokenResponse.access_token }),
        });

      if (!response.ok) {
        if (response.status === 409) {
          setErrors({
            conflict: await getAuthErrorMessage(response, "Account conflict"),
          });
        } else {
          setErrors({
            submit: await getAuthErrorMessage(
              response,
              "Google authentication failed",
            ),
          });
        }
        return;
      }

      const payload = await response.json().catch(() => null);
      const internalToken =
        payload &&
        typeof payload === "object" &&
        "access_token" in payload &&
        typeof payload.access_token === "string"
          ? payload.access_token
          : "";

      if (!internalToken) {
        setErrors({
          submit: "The authentication service returned an invalid session.",
        });
        return;
      }

      setAuthToken(internalToken);
      setSuccess(true);
      setTimeout(() => {
        navigate("/");
      }, 1000);
    } catch {
      setErrors({ submit: "Unable to connect to the authentication service." });
    } finally {
      setLoading(false);
    }
  },
  onError: () => handleGoogleError(),
  });

  const handleGoogleError = () => {
    setErrors({ submit: "Google sign-in failed. Please try again." });
  };

  const handleMicrosoftSuccess = useCallback(() => {
    setSuccess(true);
    setTimeout(() => {
      navigate("/");
    }, 1000);
  }, [navigate]);

  const handleMicrosoftError = useCallback((message: string) => {
    setErrors({ submit: message });
  }, []);

  const pwMatch =
    password.length > 0 && confirmPw.length > 0 && password === confirmPw;

  return (
    <motion.div
      key="register"
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -14 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
      className="w-full max-w-[456px] relative z-10"
    >
      <div className="relative bg-white rounded-2xl overflow-hidden border border-slate-700/80 shadow-[0_0_50px_-10px_rgba(14,165,233,0.3)]">
        {/* Sleek Dark Card Header */}
        <div className="bg-[#0b132a] px-6 sm:px-8 py-7 sm:py-8 border-b border-slate-800/80 flex items-center justify-between">
          <LogSentinelLogo large dark />
        </div>

        {/* Form content */}
        <div className="p-6 sm:p-7">
          <div className="mb-4 text-left">
            <h1 className="text-xl font-bold text-slate-900 tracking-tight leading-tight select-none">
              Create account
            </h1>
            <p className="text-xs text-slate-600 mt-1 font-medium select-none">
              Start monitoring your infrastructure in minutes
            </p>
          </div>

          {errors.submit && (
            <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs flex items-center gap-2 select-none text-left shadow-sm">
              <AlertCircle className="w-4 h-4 shrink-0 text-red-600" />
              <span className="font-medium">{errors.submit}</span>
            </div>
          )}
          {errors.conflict && (
            <div className="mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-center gap-2 select-none text-left shadow-sm">
              <AlertCircle className="w-4 h-4 shrink-0 text-amber-600" />
              <span className="font-medium">{errors.conflict}</span>
            </div>
          )}

          {success ? (
            <SuccessState
              title="Account created!"
              body="Your workspace is ready. Redirecting you to the dashboard..."
            />
          ) : (
            <form onSubmit={handleSubmit} noValidate autoComplete="off" className="space-y-3">

              {/* Row 1: Name + Email */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-start">
                <InputField
                  label="Full name"
                  type="text"
                  value={fullName}
                  onChange={setFullName}
                  placeholder="Alex Chen"
                  icon={<User className="w-4 h-4 text-slate-400" />}
                  error={errors.fullName}
                  disabled={loading}
                />
                <InputField
                  label="Work email"
                  type="email"
                  value={email}
                  onChange={setEmail}
                  placeholder="you@company.com"
                  icon={<Mail className="w-4 h-4 text-slate-400" />}
                  error={errors.email}
                  disabled={loading}
                />
              </div>

              <InputField
                label="Organization"
                type="text"
                value={org}
                onChange={setOrg}
                placeholder="Acme Corp"
                icon={<Building2 className="w-4 h-4 text-slate-400" />}
                optional
                disabled={loading}
              />

              {/* Row 2: Password + Confirm */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-start">
                <div className="text-left">
                  <InputField
                    label="Password"
                    type={showPw ? "text" : "password"}
                    value={password}
                    onChange={setPassword}
                    placeholder="Min. 8 chars"
                    icon={<Lock className="w-4 h-4 text-slate-400" />}
                    error={errors.password}
                    disabled={loading}
                    rightElement={
                      <button
                        type="button"
                        onClick={() => setShowPw((v) => !v)}
                        className="text-slate-400 hover:text-slate-700 transition-colors cursor-pointer p-0.5"
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
                  icon={<Lock className="w-4 h-4 text-slate-400" />}
                  error={errors.confirmPw}
                  success={pwMatch}
                  disabled={loading}
                  rightElement={
                    <button
                      type="button"
                      onClick={() => setShowConfirm((v) => !v)}
                      className="text-slate-400 hover:text-slate-700 transition-colors cursor-pointer p-0.5"
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
                    disabled={loading}
                    className="sr-only"
                  />
                  <div
                    className={[
                      "mt-0.5 w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 transition-all duration-150",
                      agreed
                        ? "bg-sky-500 border-sky-500"
                        : errors.agreed
                        ? "border-red-400 bg-red-50/20"
                        : "border-slate-300 bg-white group-hover:border-slate-400",
                    ].join(" ")}
                  >
                    {agreed && (
                      <Check className="w-3 h-3 text-white" strokeWidth={3} />
                    )}
                  </div>
                  <span className="text-xs text-slate-700 font-medium leading-relaxed select-none">
                    I agree to the{" "}
                    <button
                      type="button"
                      className="text-sky-600 hover:text-sky-700 font-semibold transition-colors underline-offset-2 hover:underline select-none cursor-pointer"
                    >
                      Terms of Service
                    </button>{" "}
                    and{" "}
                    <button
                      type="button"
                      className="text-sky-600 hover:text-sky-700 font-semibold transition-colors underline-offset-2 hover:underline select-none cursor-pointer"
                    >
                      Privacy Policy
                    </button>
                  </span>
                </label>
                {errors.agreed && (
                  <p className="flex items-center gap-1.5 text-xs text-red-600 font-medium mt-1 ml-6 select-none">
                    <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
                    {errors.agreed}
                  </p>
                )}
              </div>

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
            <div className="mt-4">
              <div className="flex items-center gap-3 mb-3" aria-hidden="true">
                <div className="flex-1 h-px bg-slate-200" />
                <span className="text-[10px] font-bold tracking-wider text-slate-500 uppercase select-none">
                  OR CONTINUE WITH
                </span>
                <div className="flex-1 h-px bg-slate-200" />
              </div>

              <div className="space-y-2">
                {googleClientId ? (
                  loading ? (
                    <SSOButton
                      provider={{
                        id: "Google",
                        label: "Continue with Google",
                        icon: <GoogleIcon />,
                        onLogin: () => undefined,
                        disabled: true,
                      }}
                    />
                  ) : (
                    <SSOButton
                      provider={{
                        id: "Google",
                        label: "Continue with Google",
                        icon: <GoogleIcon />,
                        onLogin: () => googleLogin(),
                        disabled: loading,
                      }}
                    />
                  )
                ) : (
                  <SSOButton
                    provider={{
                      id: "Google",
                      label: "Continue with Google",
                      icon: <GoogleIcon />,
                      onLogin: () => undefined,
                      disabled: true,
                      title: "Google sign-in is not configured.",
                    }}
                  />
                )}

                <MicrosoftRegisterEntry
                  disabled={loading}
                  onSuccess={handleMicrosoftSuccess}
                  onError={handleMicrosoftError}
                />

                <FeatureFlag flag="ENABLE_GITHUB_AUTH">
                  <SSOButton
                    provider={{
                      id: "GitHub",
                      label: "Continue with GitHub",
                      icon: <GitHubIcon />,
                      onLogin: () => {
                        const apiBase = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");
                        window.location.href = `${apiBase}/api/auth/github`;
                      },
                      disabled: loading,
                      bgClass: "bg-[#181d24]",
                      borderClass: "border-[#181d24]",
                      textClass: "text-white",
                      hoverClass: "hover:bg-[#22272e] hover:border-[#22272e]",
                    }}
                  />
                </FeatureFlag>
              </div>
            </div>
          )}

          <div className="mt-4 pt-4 border-t border-slate-100">
            <p className="text-xs text-center text-slate-600 font-medium select-none">
              Already have an account?{" "}
              <button
                type="button"
                onClick={() => navigate("/login")}
                disabled={loading}
                className="text-sky-600 hover:text-sky-700 font-bold transition-colors select-none cursor-pointer"
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

