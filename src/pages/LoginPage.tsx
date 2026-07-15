import { useState } from "react";
import { useNavigate } from "react-router";
import {
  Eye,
  EyeOff,
  Mail,
  Lock,
  ArrowRight,
  Check,
} from "lucide-react";
import { motion } from "motion/react";
import {
  LogSentinelLogo,
  InputField,
  SSOSection,
  SuccessState,
} from "./AuthShared";

export function LoginPage() {
  const navigate = useNavigate();
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
    
    // Set logged in flag
    localStorage.setItem("isLoggedIn", "true");

    // Redirect to dashboard after a short delay showing the success state
    setTimeout(() => {
      navigate("/");
    }, 1000);
  };

  const handleSSO = () => {
    // Set logged in flag and redirect
    localStorage.setItem("isLoggedIn", "true");
    navigate("/");
  };

  return (
    <motion.div
      key="login"
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -14 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
      className="w-full max-w-[420px] relative z-10"
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
                    className="text-[13px] text-sky-600 hover:text-sky-700 font-medium transition-colors select-none cursor-pointer"
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
                    "transition-all duration-150 select-none cursor-pointer",
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
                onGoogle={handleSSO}
                onMicrosoft={handleSSO}
                onGitHub={handleSSO}
              />
            )}

            <div className="mt-6 pt-5 border-t border-slate-200">
              <p className="text-[13px] text-center text-slate-600 select-none">
                No account yet?{" "}
                <button
                  onClick={() => navigate("/register")}
                  className="text-sky-600 hover:text-sky-700 font-medium transition-colors select-none cursor-pointer"
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
