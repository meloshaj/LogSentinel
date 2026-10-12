import { broadcastResponseToMainFrame } from "@azure/msal-browser/redirect-bridge";

if (window.opener !== null || window.parent !== window) {
  broadcastResponseToMainFrame().catch((error: unknown) => {
    // Report only the error class; messages can contain redirect parameters.
    console.error(
      "Redirect bridge communication failed",
      error instanceof Error ? error.name : "UnknownError",
    );
  });
}
