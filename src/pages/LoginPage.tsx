import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useNavigate } from "react-router";
import {
  AlertCircle,
  ArrowRight,
  Check,
  Eye,
  EyeOff,
  Lock,
  Mail,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { GoogleLogin } from "@react-oauth/google";
import {
  GoogleIcon,
  GitHubIcon,
  InputField,
  LogSentinelLogo,
  MicrosoftIcon,
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

type AuthOperation = "email" | "google" | "microsoft";

interface MicrosoftButtonProps {
  rememberMe: boolean;
  disabled: boolean;
  onOperationStart: () => boolean;
  onOperationEnd: () => void;
  onSuccess: () => void;
  onError: (message: string) => void;
}

function ConfiguredMicrosoftButton({
  rememberMe,
  disabled,
  onOperationStart,
  onOperationEnd,
  onSuccess,
  onError,
}: MicrosoftButtonProps) {
  const microsoftAuth = useMicrosoftAuth();

  const handleLogin = async () => {
    if (!onOperationStart()) return;

    try {
      const result = await microsoftAuth.login(rememberMe);
      if (result.success) {
        onSuccess();
      } else if (result.error) {
        onError(result.error);
      }
    } finally {
      onOperationEnd();
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

function UnavailableMicrosoftButton({
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
          descriptionId: "microsoft-login-availability",
          title: message,
        }}
      />
      <p
        id="microsoft-login-availability"
        className="px-1 text-[11px] text-slate-600"
      >
        {message}
      </p>
    </div>
  );
}

function MicrosoftLoginEntry(props: MicrosoftButtonProps) {
  const status = useMicrosoftAuthStatus();

  if (status === "ready") {
    return <ConfiguredMicrosoftButton {...props} />;
  }

  return <UnavailableMicrosoftButton status={status} />;
}

export function LoginPage() {
  const navigate = useNavigate();
  const reduceMotion = useReducedMotion();
  const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [operation, setOperation] = useState<AuthOperation | null>(null);
  const [success, setSuccess] = useState(false);
  const operationRef = useRef<AuthOperation | null>(null);
  const errorSummaryRef = useRef<HTMLDivElement>(null);
  const successRef = useRef<HTMLDivElement>(null);
  const navigationTimerRef = useRef<number | null>(null);
  const isLoading = operation !== null;

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    const errorMsg = params.get("error");
    if (token) {
      setAuthToken(token, rememberMe);
      setSuccess(true);
      navigationTimerRef.current = window.setTimeout(() => {
        navigate("/");
      }, 1500);
      // Clean up URL
      window.history.replaceState({}, document.title, window.location.pathname);
    } else if (errorMsg) {
      if (errorMsg.includes("different provider")) {
        setErrors({ conflict: decodeURIComponent(errorMsg) });
      } else {
        setErrors({ submit: decodeURIComponent(errorMsg) });
      }
      // Clean up URL
      window.history.replaceState({}, document.title, window.location.pathname);
    }

    return () => {
      if (navigationTimerRef.current !== null) {
        window.clearTimeout(navigationTimerRef.current);
      }
    };
  }, [navigate, rememberMe]);

  useEffect(() => {
    if (errors.submit) {
      errorSummaryRef.current?.focus();
      return;
    }

    const firstInvalidId = errors.email
      ? "email-address"
      : errors.password
        ? "password"
        : null;
    if (firstInvalidId) {
      document.getElementById(firstInvalidId)?.focus();
    }
  }, [errors]);

  useEffect(() => {
    if (success) successRef.current?.focus();
  }, [success]);

  const beginOperation = useCallback((nextOperation: AuthOperation) => {
    if (operationRef.current) return false;
    operationRef.current = nextOperation;
    setOperation(nextOperation);
    setErrors({});
    return true;
  }, []);

  const endOperation = useCallback((completedOperation: AuthOperation) => {
    if (operationRef.current !== completedOperation) return;
    operationRef.current = null;
    setOperation(null);
  }, []);

  const completeLogin = useCallback(() => {
    setSuccess(true);
    navigationTimerRef.current = window.setTimeout(() => {
      navigate("/");
    }, 1000);
  }, [navigate]);

  const validate = () => {
    const nextErrors: Record<string, string> = {};
    const normalizedEmail = email.trim().toLowerCase();
    if (!normalizedEmail) {
      nextErrors.email = "Email address is required";
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      nextErrors.email = "Enter a valid email address";
    }

    if (!password) {
      nextErrors.password = "Password is required";
    } else if (password.length < 8) {
      nextErrors.password = "Password must be at least 8 characters";
    }
    return nextErrors;
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (operationRef.current) return;

    const validationErrors = validate();
    if (Object.keys(validationErrors).length) {
      setErrors(validationErrors);
      return;
    }

    if (!beginOperation("email")) return;

    try {
      const apiBase = (
        import.meta.env.VITE_API_URL || "http://localhost:8000"
      ).replace(/\/+$/, "");
      const response = await fetch(`${apiBase}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          password,
        }),
      });

      if (!response.ok) {
        if (response.status === 409) {
          setErrors({
            conflict: await getAuthErrorMessage(response, "Account conflict"),
          });
        } else {
          setErrors({
            submit: await getAuthErrorMessage(response, "Authentication failed"),
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

      setAuthToken(internalToken, rememberMe);
      completeLogin();
    } catch {
      setErrors({ submit: "Unable to connect to the authentication service." });
    } finally {
      endOperation("email");
    }
  };

  const handleGoogleSuccess = async (credentialResponse: unknown) => {
    if (operationRef.current) return;

    const credential =
      typeof credentialResponse === "object" &&
      credentialResponse !== null &&
      "credential" in credentialResponse &&
      typeof credentialResponse.credential === "string"
        ? credentialResponse.credential
        : "";
    if (!credential) {
      setErrors({ submit: "Google sign-in did not return a valid credential." });
      return;
    }

    if (!beginOperation("google")) return;

    try {
      const apiBase = (
        import.meta.env.VITE_API_URL || "http://localhost:8000"
      ).replace(/\/+$/, "");
      const response = await fetch(`${apiBase}/api/auth/google`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential }),
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

      setAuthToken(internalToken, rememberMe);
      completeLogin();
    } catch {
      setErrors({ submit: "Unable to connect to the authentication service." });
    } finally {
      endOperation("google");
    }
  };

  const handleGoogleError = () => {
    if (operationRef.current) return;
    setErrors({ submit: "Google sign-in failed. Please try again." });
  };

  const handleMicrosoftSuccess = () => {
    completeLogin();
  };

  const handleMicrosoftError = (message: string) => {
    setErrors({ submit: message });
  };

  return (
    <motion.div
      key="login"
      initial={reduceMotion ? false : { opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduceMotion ? undefined : { opacity: 0, y: -14 }}
      transition={{ duration: reduceMotion ? 0 : 0.28, ease: "easeOut" }}
      className="w-full max-w-[420px] relative z-10"
    >
      <div className="relative bg-white rounded-2xl overflow-hidden border border-slate-700/80 shadow-[0_0_50px_-10px_rgba(14,165,233,0.3)]">
        {/* Sleek Dark Card Header */}
        <div className="bg-[#0b132a] px-6 sm:px-8 py-7 sm:py-8 border-b border-slate-800/80 flex items-center justify-between">
          <LogSentinelLogo large dark />
        </div>

        <div className="p-6 sm:p-7">
          <div className="mb-4 text-left">
            <h1 className="text-xl font-bold text-slate-900 tracking-tight leading-tight select-none">
              Welcome back
            </h1>
            <p className="text-xs text-slate-600 mt-1 font-medium select-none">
              Sign in to your workspace to continue
            </p>
          </div>

          {errors.submit && (
            <div
              ref={errorSummaryRef}
              tabIndex={-1}
              role="alert"
              aria-live="assertive"
              className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs flex items-start gap-2 text-left shadow-sm"
            >
              <AlertCircle className="w-4 h-4 shrink-0 text-red-600 mt-0.5" aria-hidden="true" />
              <span className="font-medium">{errors.submit}</span>
            </div>
          )}
          {errors.conflict && (
            <div
              ref={errorSummaryRef}
              tabIndex={-1}
              role="alert"
              aria-live="assertive"
              className="mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2 text-left shadow-sm"
            >
              <AlertCircle className="w-4 h-4 shrink-0 text-amber-600 mt-0.5" aria-hidden="true" />
              <span className="font-medium">{errors.conflict}</span>
            </div>
          )}

          {success ? (
            <div
              ref={successRef}
              tabIndex={-1}
              className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 rounded-lg"
            >
              <SuccessState
                title="Signed in successfully"
                body="Redirecting you to your dashboard..."
              />
            </div>
          ) : (
            <form
              onSubmit={handleSubmit}
              noValidate
              autoComplete="off"
              className="space-y-3.5"
              aria-busy={isLoading}
            >
              <InputField
                id="email-address"
                name="email"
                label="Email address"
                type="email"
                value={email}
                onChange={setEmail}
                autoComplete="one-time-code"
                inputMode="email"
                placeholder="you@company.com"
                icon={<Mail className="w-4 h-4 text-slate-400" aria-hidden="true" />}
                error={errors.email}
                disabled={isLoading}
                required
              />

              <InputField
                id="password"
                name="password"
                label="Password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={setPassword}
                autoComplete="new-password"
                placeholder="••••••••"
                icon={<Lock className="w-4 h-4 text-slate-400" aria-hidden="true" />}
                error={errors.password}
                disabled={isLoading}
                required
                rightElement={

                  <button
                    type="button"
                    onClick={() => setShowPassword((visible) => !visible)}
                    className="text-slate-400 hover:text-slate-700 transition-colors cursor-pointer rounded p-0.5"
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    aria-pressed={showPassword}
                    disabled={isLoading}
                  >
                    {showPassword ? (
                      <EyeOff className="w-4 h-4" aria-hidden="true" />
                    ) : (
                      <Eye className="w-4 h-4" aria-hidden="true" />
                    )}
                  </button>
                }
              />

              <div className="flex items-center justify-between gap-3 pt-0.5">
                <label className="flex items-center gap-2 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={rememberMe}
                    onChange={(event) => setRememberMe(event.target.checked)}
                    disabled={isLoading}
                    className="sr-only peer"
                  />
                  <span
                    aria-hidden="true"
                    className={[
                      "w-4 h-4 rounded border flex items-center justify-center transition-all duration-150 flex-shrink-0",
                      rememberMe
                        ? "bg-sky-500 border-sky-500"
                        : isLoading
                          ? "border-slate-300 bg-slate-100"
                          : "border-slate-300 bg-white group-hover:border-slate-400",
                    ].join(" ")}
                  >
                    {rememberMe && (
                      <Check
                        className="w-3 h-3 text-white"
                        strokeWidth={3}
                      />
                    )}
                  </span>
                  <span className="text-xs text-slate-700 font-semibold select-none">
                    Remember me
                  </span>
                </label>
                <button
                  type="button"
                  onClick={() => navigate("/forgot-password")}
                  className="text-xs text-sky-600 hover:text-sky-700 font-semibold transition-colors select-none cursor-pointer disabled:opacity-60"
                  disabled={isLoading}
                >
                  Forgot password?
                </button>
              </div>

              <button
                type="submit"
                disabled={isLoading}
                aria-busy={operation === "email" || undefined}
                className={[
                  "w-full py-2.5 rounded-lg text-sm font-bold",
                  "flex items-center justify-center gap-2 mt-2",
                  "transition-all duration-150 select-none cursor-pointer shadow-md",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-1",
                  isLoading
                    ? "bg-sky-100 text-sky-400 cursor-not-allowed shadow-none"
                    : "bg-sky-500 hover:bg-sky-400 text-white shadow-sky-500/20 active:scale-[0.99]",
                ].join(" ")}
              >
                {operation === "email" ? (
                  <>
                    <Spinner /> Signing in…
                  </>
                ) : (
                  <>
                    Sign In <ArrowRight className="w-4 h-4" aria-hidden="true" />
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
                  operation ? (
                    <SSOButton
                      provider={{
                        id: "Google",
                        label:
                          operation === "google"
                            ? "Signing in with Google…"
                            : "Continue with Google",
                        icon: <GoogleIcon />,
                        onLogin: () => undefined,
                        disabled: true,
                        loading: operation === "google",
                      }}
                    />
                  ) : (
                    <div className="w-full flex justify-center">
                      <GoogleLogin
                        onSuccess={handleGoogleSuccess}
                        onError={handleGoogleError}
                        theme="outline"
                        size="large"
                        width={364}
                        text="continue_with"
                        shape="rectangular"
                      />
                    </div>
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

                <MicrosoftLoginEntry
                  rememberMe={rememberMe}
                  disabled={isLoading}
                  onOperationStart={() => beginOperation("microsoft")}
                  onOperationEnd={() => endOperation("microsoft")}
                  onSuccess={handleMicrosoftSuccess}
                  onError={handleMicrosoftError}
                />

                <SSOButton
                  provider={{
                    id: "GitHub",
                    label: "Continue with GitHub",
                    icon: <GitHubIcon />,
                    onLogin: () => {
                      const apiBase = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");
                      window.location.href = `${apiBase}/api/auth/github`;
                    },
                    disabled: isLoading,
                    bgClass: "bg-[#181d24]",
                    borderClass: "border-[#181d24]",
                    textClass: "text-white",
                    hoverClass: "hover:bg-[#22272e] hover:border-[#22272e]",
                  }}
                />
              </div>
            </div>
          )}

          <div className="mt-4 pt-4 border-t border-slate-100">
            <p className="text-xs text-center text-slate-600 font-medium select-none">
              No account yet?{" "}
              <button
                type="button"
                onClick={() => navigate("/register")}
                disabled={isLoading}
                className="text-sky-600 hover:text-sky-700 font-bold transition-colors select-none cursor-pointer disabled:opacity-60"
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

