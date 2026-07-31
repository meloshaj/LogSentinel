import { broadcastResponseToMainFrame } from "@azure/msal-browser/redirect-bridge";

broadcastResponseToMainFrame().catch((e) => {
  // Catch but do not log sensitive data or token fragments
  console.error("Redirect bridge communication failed", e.name);
});
