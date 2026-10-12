import { broadcastResponseToMainFrame } from "@azure/msal-browser/redirect-bridge";

const root = typeof document !== "undefined" ? document.getElementById("root") : null;
if (root) {
  root.innerHTML = `
    <div style="min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; background: #0d1117; color: #58a6ff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; gap: 14px;">
      <div style="width: 28px; height: 28px; border: 3px solid #30363d; border-top-color: #388bfd; border-radius: 50%; animation: spin 0.8s linear infinite;"></div>
      <p style="font-size: 13px; color: #8b949e; margin: 0;">Completing sign-in...</p>
      <button onclick="window.close()" style="margin-top: 10px; padding: 6px 14px; background: #21262d; border: 1px solid #30363d; color: #c9d1d9; border-radius: 6px; font-size: 12px; cursor: pointer;">Close window</button>
      <style>@keyframes spin { to { transform: rotate(360deg); } }</style>
    </div>
  `;
}

broadcastResponseToMainFrame()
  .then(() => {
    try {
      window.close();
    } catch {}
  })
  .catch((error: unknown) => {
    // Report only the error class; messages can contain redirect parameters.
    console.error(
      "Redirect bridge communication failed",
      error instanceof Error ? error.name : "UnknownError",
    );
  });

