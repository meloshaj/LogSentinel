import { GoogleOAuthProvider, useGoogleLogin, type TokenResponse } from "@react-oauth/google";
import { ErrorBoundary } from "../common/ErrorBoundary";
import {
  getGoogleClientId,
  isGoogleAuthEnabled,
} from "../../config/googleAuth";
import { GoogleIcon, SSOButton } from "../../pages/AuthShared";

interface GoogleLoginButtonProps {
  disabled: boolean;
  loading: boolean;
  label: string;
  onSuccess: (response: TokenResponse) => void | Promise<void>;
  onError: () => void;
}

function UnavailableGoogleLoginButton({
  label,
  message,
}: Pick<GoogleLoginButtonProps, "label"> & { message: string }) {
  return (
    <div className="space-y-1">
      <SSOButton
        provider={{
          id: "Google",
          label,
          icon: <GoogleIcon />,
          onLogin: () => undefined,
          disabled: true,
          descriptionId: "google-login-availability",
          title: message,
        }}
      />
      <p
        id="google-login-availability"
        className="px-1 text-[11px] text-slate-600"
        role="status"
      >
        {message}
      </p>
    </div>
  );
}

function ConfiguredGoogleLoginButton({
  disabled,
  loading,
  label,
  onSuccess,
  onError,
}: GoogleLoginButtonProps) {
  const login = useGoogleLogin({ onSuccess, onError });

  return (
    <SSOButton
      provider={{
        id: "Google",
        label,
        icon: <GoogleIcon />,
        onLogin: login,
        disabled,
        loading,
      }}
    />
  );
}

export function GoogleLoginButton(props: GoogleLoginButtonProps) {
  const googleClientId = getGoogleClientId();
  if (!isGoogleAuthEnabled() || !googleClientId) {
    return (
      <UnavailableGoogleLoginButton
        label={props.label}
        message="Google sign-in is not configured."
      />
    );
  }

  return (
    <ErrorBoundary
      fallback={
        <p className="px-1 text-[11px] text-slate-500" role="status">
          Google integration is unavailable.
        </p>
      }
    >
      <GoogleOAuthProvider clientId={googleClientId}>
        <ConfiguredGoogleLoginButton {...props} />
      </GoogleOAuthProvider>
    </ErrorBoundary>
  );
}
