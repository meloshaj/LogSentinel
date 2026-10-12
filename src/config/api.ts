/** Return the configured backend API origin, or an empty string if invalid. */
export function getApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_URL?.trim();
  if (!configured) return "";

  try {
    const url = new URL(configured);
    const isLoopback =
      url.hostname === "localhost" || url.hostname === "127.0.0.1";

    if (
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== "/" ||
      (url.protocol !== "https:" && !isLoopback)
    ) {
      return "";
    }

    return url.origin;
  } catch {
    return "";
  }
}

/** Resolve a same-backend API path using the configured production origin. */
export function getApiUrl(path: string): string {
  const base = getApiBaseUrl();
  if (!base) {
    throw new Error("VITE_API_URL must be configured with the HTTPS backend origin");
  }
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error("API paths must be root-relative paths");
  }
  return base + path;
}
