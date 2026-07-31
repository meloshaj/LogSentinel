import { useState, useCallback } from "react";
import { useMsal } from "@azure/msal-react";
import { loginRequest, isMsalConfigured } from "../config/msal";
import { setAuthToken, clearAuthToken } from "../utils/auth";
import { useNavigate } from "react-router";
import { InteractionRequiredAuthError } from "@azure/msal-browser";

export function useMicrosoftAuth() {
  const { instance, accounts } = useMsal();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const login = useCallback(async (rememberMe: boolean = false): Promise<{ success: boolean; error?: string }> => {
    if (!isMsalConfigured()) {
      const msg = "Microsoft login is not currently configured";
      setError(msg);
      return { success: false, error: msg };
    }

    setLoading(true);
    setError(null);

    try {
      let microsoftAccessToken = "";
      
      const activeAccount = instance.getActiveAccount();
      const account = activeAccount || accounts[0] || null;

      if (account) {
        // Ensure active account is set if we have one
        if (instance.getActiveAccount()?.homeAccountId !== account.homeAccountId) {
          instance.setActiveAccount(account);
        }
        
        try {
          const silentResponse = await instance.acquireTokenSilent({
            ...loginRequest,
            account,
          });
          microsoftAccessToken = silentResponse.accessToken;
        } catch (silentError) {
          if (silentError instanceof InteractionRequiredAuthError) {
            const popupResponse = await instance.acquireTokenPopup({
              ...loginRequest,
              account,
            });
            microsoftAccessToken = popupResponse.accessToken;
            instance.setActiveAccount(popupResponse.account);
          } else {
            throw silentError;
          }
        }
      } else {
        const popupResponse = await instance.loginPopup({
          ...loginRequest,
          prompt: "select_account"
        });
        
        if (popupResponse.account) {
          instance.setActiveAccount(popupResponse.account);
        }
        
        microsoftAccessToken = popupResponse.accessToken;
        
        if (!microsoftAccessToken && popupResponse.account) {
          // If loginPopup somehow doesn't return an access token but returns an account,
          // try silent acquisition as a fallback
          try {
            const silentResp = await instance.acquireTokenSilent({
              ...loginRequest,
              account: popupResponse.account
            });
            microsoftAccessToken = silentResp.accessToken;
          } catch (err) {
            if (err instanceof InteractionRequiredAuthError) {
              const interactiveResp = await instance.acquireTokenPopup({
                ...loginRequest,
                account: popupResponse.account
              });
              microsoftAccessToken = interactiveResp.accessToken;
            } else {
              throw err;
            }
          }
        }
      }

      if (!microsoftAccessToken) {
        throw new Error("No access token received from Microsoft");
      }

      // Send token to LogSentinel backend
      const apiBase = import.meta.env.VITE_API_URL || "http://localhost:8000";
      const response = await fetch(`${apiBase}/api/auth/microsoft`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ access_token: microsoftAccessToken }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        const detail = data.detail;
        let errorMessage = "Microsoft authentication could not be verified";
        
        switch (detail) {
          case "microsoft_auth_disabled":
            errorMessage = "Microsoft login is not currently configured";
            break;
          case "invalid_microsoft_token":
            errorMessage = "Microsoft authentication could not be verified";
            break;
          case "invalid_microsoft_tenant":
            errorMessage = "this Microsoft organization is not allowed";
            break;
          case "missing_required_scope":
            errorMessage = "required LogSentinel API permission is missing";
            break;
          case "account_linking_required":
            errorMessage = "an existing LogSentinel account must be explicitly linked";
            break;
          case "microsoft_identity_conflict":
            errorMessage = "Microsoft identity conflicts with an existing mapping";
            break;
          case "microsoft_onboarding_required":
            errorMessage = "additional account information is required";
            break;
          case "microsoft_jwks_unavailable":
            errorMessage = "Microsoft verification is temporarily unavailable";
            break;
          default:
            if (response.status === 503) {
              errorMessage = "Microsoft verification is temporarily unavailable";
            } else if (typeof detail === "string") {
              errorMessage = detail;
            }
            break;
        }
        setError(errorMessage);
        setLoading(false);
        return { success: false, error: errorMessage };
      }

      const data = await response.json();
      
      if (typeof data.access_token !== "string" || !data.access_token) {
        const msg = "Authentication server returned an invalid internal token";
        setError(msg);
        setLoading(false);
        return { success: false, error: msg };
      }

      // Store internal JWT and finalize (using Remember Me)
      setAuthToken(data.access_token, rememberMe);
      setLoading(false);
      return { success: true };

    } catch (err: any) {
      let msg = "Microsoft authentication could not be verified";
      if (err.name === "BrowserAuthError" && err.errorCode === "user_cancelled") {
        msg = ""; // Neutral cancellation
        setError(null); 
      } else if (err.name === "BrowserAuthError" && err.errorCode === "popup_window_error") {
        msg = "tell the user to allow popups";
        setError(msg);
      } else if (err.name === "BrowserAuthError" && err.errorCode === "consent_required") {
        msg = "explain that LogSentinel permission must be granted";
        setError(msg);
      } else if (!window.navigator.onLine || err.message === "Failed to fetch") {
        msg = "connection failed; user may retry";
        setError(msg);
      } else {
        setError(msg);
      }
      setLoading(false);
      return { success: false, error: msg };
    }
  }, [instance, accounts]);

  return { login, loading, error };
}
