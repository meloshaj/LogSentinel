import { useCallback, useRef, useState } from "react";
import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { useMsal } from "@azure/msal-react";
import { isMsalConfigured, loginRequest } from "../config/msal";
import { setAuthToken } from "../utils/auth";

export interface MicrosoftLoginResult {
  success: boolean;
  error?: string;
  cancelled?: boolean;
}

const BACKEND_ERROR_MESSAGES: Record<string, string> = {
  microsoft_auth_disabled: "Microsoft sign-in is not currently configured.",
  invalid_microsoft_token:
    "Microsoft sign-in could not be verified. Please try again.",
  invalid_microsoft_tenant:
    "This Microsoft organization is not allowed to use LogSentinel.",
  missing_required_scope:
    "LogSentinel API permission is missing. Ask an administrator to grant access.",
  account_linking_required:
    "An existing LogSentinel account uses this email. Link accounts explicitly before using Microsoft sign-in.",
  microsoft_identity_conflict:
    "This Microsoft identity conflicts with an existing account mapping. Contact an administrator.",
  microsoft_onboarding_required:
    "Microsoft did not provide the account information required to create a LogSentinel user.",
  microsoft_jwks_unavailable:
    "Microsoft verification is temporarily unavailable. Please try again later.",
};

export function mapMicrosoftBackendError(
  status: number,
  detail: unknown,
): string {
  if (typeof detail === "string" && BACKEND_ERROR_MESSAGES[detail]) {
    return BACKEND_ERROR_MESSAGES[detail];
  }

  switch (status) {
    case 401:
      return "Microsoft sign-in could not be verified. Please try again.";
    case 403:
      return "This Microsoft account does not have permission to use LogSentinel.";
    case 409:
      return "This Microsoft account cannot be connected automatically. Contact an administrator.";
    case 503:
      return "Microsoft sign-in is temporarily unavailable. Please try again later.";
    default:
      return "Microsoft sign-in could not be completed. Please try again.";
  }
}

function readErrorCode(error: unknown): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "errorCode" in error &&
    typeof error.errorCode === "string"
  ) {
    return error.errorCode.toLowerCase();
  }
  return "";
}

function isFailedFetch(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "message" in error &&
    error.message === "Failed to fetch"
  );
}

export function mapMicrosoftClientError(error: unknown): string | null {
  const errorCode = readErrorCode(error);

  if (errorCode === "user_cancelled" || errorCode === "user_canceled") {
    return null;
  }

  if (
    errorCode === "popup_window_error" ||
    errorCode === "empty_window_error" ||
    errorCode === "block_nested_popups"
  ) {
    return "Your browser blocked the Microsoft sign-in window. Allow pop-ups for this site and try again.";
  }

  if (errorCode === "monitor_popup_timeout") {
    return "The Microsoft sign-in window timed out. Please try again.";
  }

  if (errorCode === "consent_required") {
    return "LogSentinel API permission must be granted before Microsoft sign-in can continue.";
  }

  if (errorCode === "interaction_required" || errorCode === "login_required") {
    return "Microsoft needs you to sign in again. Please retry.";
  }

  if (
    errorCode === "client_not_initialized" ||
    errorCode === "stubbed_public_client_application_called" ||
    errorCode === "invalid_client" ||
    errorCode === "redirect_uri_mismatch"
  ) {
    return "Microsoft sign-in is unavailable because its configuration is invalid.";
  }

  if (
    (typeof navigator !== "undefined" && !navigator.onLine) ||
    isFailedFetch(error)
  ) {
    return "Unable to connect to Microsoft or the LogSentinel authentication service. Check your connection and retry.";
  }

  return "Microsoft sign-in could not be completed. Please try again.";
}

export function useMicrosoftAuth() {
  const { instance, accounts } = useMsal();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operationInFlight = useRef(false);

  const login = useCallback(
    async (rememberMe = false): Promise<MicrosoftLoginResult> => {
      if (operationInFlight.current) {
        return { success: false };
      }

      if (!isMsalConfigured()) {
        const message = "Microsoft sign-in is not currently configured.";
        setError(message);
        return { success: false, error: message };
      }

      operationInFlight.current = true;
      setLoading(true);
      setError(null);

      try {
        let microsoftAccessToken = "";
        const activeAccount = instance.getActiveAccount();
        const account = activeAccount ?? accounts[0] ?? null;

        if (account) {
          instance.setActiveAccount(account);

          try {
            const silentResponse = await instance.acquireTokenSilent({
              ...loginRequest,
              account,
            });
            microsoftAccessToken = silentResponse.accessToken;
          } catch (silentError) {
            if (!(silentError instanceof InteractionRequiredAuthError)) {
              throw silentError;
            }

            const popupResponse = await instance.acquireTokenPopup({
              ...loginRequest,
              account,
            });
            microsoftAccessToken = popupResponse.accessToken;
            instance.setActiveAccount(popupResponse.account ?? account);
          }
        } else {
          const loginResponse = await instance.loginPopup({
            ...loginRequest,
            prompt: "select_account",
          });

          if (!loginResponse.account) {
            const message =
              "Microsoft sign-in did not return an account. Please try again.";
            setError(message);
            return { success: false, error: message };
          }

          instance.setActiveAccount(loginResponse.account);
          microsoftAccessToken = loginResponse.accessToken;

          if (!microsoftAccessToken) {
            try {
              const silentResponse = await instance.acquireTokenSilent({
                ...loginRequest,
                account: loginResponse.account,
              });
              microsoftAccessToken = silentResponse.accessToken;
            } catch (silentError) {
              if (!(silentError instanceof InteractionRequiredAuthError)) {
                throw silentError;
              }

              const popupResponse = await instance.acquireTokenPopup({
                ...loginRequest,
                account: loginResponse.account,
              });
              microsoftAccessToken = popupResponse.accessToken;
              instance.setActiveAccount(
                popupResponse.account ?? loginResponse.account,
              );
            }
          }
        }

        if (!microsoftAccessToken) {
          const message =
            "Microsoft sign-in did not return an access token. Please try again.";
          setError(message);
          return { success: false, error: message };
        }

        const apiBase = (
          import.meta.env.VITE_API_URL || "http://localhost:8000"
        ).replace(/\/+$/, "");
        const response = await fetch(`${apiBase}/api/auth/microsoft`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ access_token: microsoftAccessToken }),
        });

        if (!response.ok) {
          const payload = await response.json().catch(() => null);
          const message = mapMicrosoftBackendError(
            response.status,
            payload && typeof payload === "object" && "detail" in payload
              ? payload.detail
              : undefined,
          );
          setError(message);
          return { success: false, error: message };
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
          const message =
            "The authentication service returned an invalid LogSentinel session. Please retry.";
          setError(message);
          return { success: false, error: message };
        }

        setAuthToken(internalToken, rememberMe);
        return { success: true };
      } catch (caughtError) {
        const message = mapMicrosoftClientError(caughtError);
        setError(message);
        return message
          ? { success: false, error: message }
          : { success: false, cancelled: true };
      } finally {
        operationInFlight.current = false;
        setLoading(false);
      }
    },
    [accounts, instance],
  );

  return { login, loading, error };
}
