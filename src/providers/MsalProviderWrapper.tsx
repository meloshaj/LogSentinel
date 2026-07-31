import React, { useEffect, useState } from "react";
import { PublicClientApplication } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { msalInstanceConfig, isMsalConfigured } from "../config/msal";

let msalInstance: PublicClientApplication | null = null;
let msalInitialized = false;

export function MsalProviderWrapper({ children }: { children: React.ReactNode }) {
  const [isInitialized, setIsInitialized] = useState(msalInitialized);
  const [initError, setInitError] = useState<Error | null>(null);

  useEffect(() => {
    if (!isMsalConfigured()) {
      setIsInitialized(true);
      return;
    }

    if (msalInitialized && msalInstance) {
      setIsInitialized(true);
      return;
    }

    try {
      if (!msalInstance) {
        msalInstance = new PublicClientApplication(msalInstanceConfig);
      }
      
      msalInstance
        .initialize()
        .then(() => {
          msalInitialized = true;
          setIsInitialized(true);
        })
        .catch((e) => {
          console.error("MSAL Initialization failed", e);
          setInitError(e instanceof Error ? e : new Error("Failed to initialize MSAL"));
          setIsInitialized(true);
        });
    } catch (e) {
      console.error("MSAL Instantiation failed", e);
      setInitError(e instanceof Error ? e : new Error("Failed to instantiate MSAL"));
      setIsInitialized(true);
    }
  }, []);

  if (!isInitialized) {
    // Prevent rendering children while MSAL is initializing to avoid errors
    return (
      <div className="min-h-screen w-full bg-[#060c18] flex items-center justify-center text-sky-400 font-mono text-xs select-none">
        <div className="flex flex-col items-center gap-3">
          <svg className="animate-spin w-6 h-6 text-sky-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span>Initializing authentication...</span>
        </div>
      </div>
    );
  }

  // If MSAL is not configured or failed to init, we still render the app but without the provider
  if (!isMsalConfigured() || initError || !msalInstance) {
    return <>{children}</>;
  }

  return <MsalProvider instance={msalInstance}>{children}</MsalProvider>;
}
