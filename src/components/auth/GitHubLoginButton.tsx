import { GitHubIcon, SSOButton } from "../../pages/AuthShared";
import { getApiBaseUrl } from "../../config/api";

interface GitHubLoginButtonProps {
  disabled: boolean;
  onError: () => void;
  navigate?: (url: string) => void;
}

export function getGitHubOAuthStartUrl(apiBase: string): string {
  let url: URL;
  try {
    url = new URL(apiBase);
  } catch {
    throw new Error("GitHub sign-in requires a configured API origin.");
  }

  const isLoopback = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/" ||
    (url.protocol !== "https:" && !isLoopback)
  ) {
    throw new Error("GitHub sign-in requires a valid HTTPS API origin.");
  }

  return `${url.origin}/api/auth/github`;
}

export function GitHubLoginButton({
  disabled,
  onError,
  navigate = (url) => window.location.assign(url),
}: GitHubLoginButtonProps) {
  const handleLogin = () => {
    try {
      const apiBase = getApiBaseUrl();
      navigate(getGitHubOAuthStartUrl(apiBase));
    } catch {
      onError();
    }
  };

  return (
    <SSOButton
      provider={{
        id: "GitHub",
        label: "Continue with GitHub",
        icon: <GitHubIcon />,
        onLogin: handleLogin,
        disabled,
        bgClass: "bg-[#181d24]",
        borderClass: "border-[#181d24]",
        textClass: "text-white",
        hoverClass: "hover:bg-[#22272e] hover:border-[#22272e]",
      }}
    />
  );
}
