import React, { createContext, useContext, useEffect, useState } from "react";
import { PublicClientApplication } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { isMsalConfigured, msalInstanceConfig } from "../config/msal";

const MSAL_INITIALIZATION_TIMEOUT_MS = 10_000;

export type MicrosoftAuthStatus =
  | "disabled"
  | "initializing"
  | "ready"
  | "error";

const MicrosoftAuthStatusContext = createContext<MicrosoftAuthStatus>("disabled");

let msalInstance: PublicClientApplication | null = null;
let msalInitializationPromise: Promise<PublicClientApplication> | null = null;
let msalInitialized = false;
let msalInitializationFailed = false;

export function __resetMsalProviderStateForTests(): void {
  if (import.meta.env.MODE !== "test") return;
  msalInstance = null;
  msalInitializationPromise = null;
  msalInitialized = false;
  msalInitializationFailed = false;
}

function initializeMsalOnce(): Promise<PublicClientApplication> {
  if (msalInitializationPromise) {
    return msalInitializationPromise;
  }

  // Assign the promise before constructing MSAL so React Strict Mode remounts
  // cannot construct or initialize a second instance.
  msalInitializationPromise = Promise.resolve()
    .then(async () => {
      msalInstance = new PublicClientApplication(msalInstanceConfig);
      await msalInstance.initialize();
      msalInitialized = true;
      return msalInstance;
    })
    .catch(() => {
      msalInitializationFailed = true;
      throw new Error("Microsoft authentication initialization failed");
    });

  return msalInitializationPromise;
}

export function useMicrosoftAuthStatus(): MicrosoftAuthStatus {
  return useContext(MicrosoftAuthStatusContext);
}

export async function clearMicrosoftAuthCache(): Promise<void> {
  if (!msalInitialized || !msalInstance) {
    return;
  }

  try {
    await msalInstance.clearCache();
    msalInstance.setActiveAccount(null);
  } catch {
    // The LogSentinel JWT is cleared independently and remains the access gate.
    console.error("Microsoft authentication cache could not be cleared");
  }
}

export function MsalProviderWrapper({ children }: { children: React.ReactNode }) {
  const configured = isMsalConfigured();
  const [status, setStatus] = useState<MicrosoftAuthStatus>(() => {
    if (!configured) return "disabled";
    if (msalInitialized && msalInstance) return "ready";
    if (msalInitializationFailed) return "error";
    return "initializing";
  });

  useEffect(() => {
    if (!configured) {
      setStatus("disabled");
      return;
    }

    if (msalInitialized && msalInstance) {
      setStatus("ready");
      return;
    }

    if (msalInitializationFailed) {
      setStatus("error");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      if (!cancelled && !msalInitialized) {
        setStatus("error");
      }
    }, MSAL_INITIALIZATION_TIMEOUT_MS);

    void initializeMsalOnce().then(
      () => {
        window.clearTimeout(timeoutId);
        if (!cancelled) setStatus("ready");
      },
      () => {
        window.clearTimeout(timeoutId);
        console.error("Microsoft authentication initialization failed");
        if (!cancelled) setStatus("error");
      },
    );

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [configured]);

  if (status === "initializing") {
    return (
      <div
        className="min-h-screen w-full bg-[#060c18] flex items-center justify-center text-sky-400 font-mono text-xs select-none"
        role="status"
        aria-live="polite"
      >
        <div className="flex flex-col items-center gap-3">
          <svg
            className="animate-spin w-6 h-6 text-sky-500"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          <span>Initializing authentication...</span>
        </div>
      </div>
    );
  }

  const content = (
    <MicrosoftAuthStatusContext.Provider value={status}>
      {children}
    </MicrosoftAuthStatusContext.Provider>
  );

  if (status !== "ready" || !msalInstance) {
    return content;
  }

  return <MsalProvider instance={msalInstance}>{content}</MsalProvider>;
}
