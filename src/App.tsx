import { ReactFlowProvider } from "@xyflow/react";
import { RouterProvider } from "react-router";
import { GoogleOAuthProvider } from "@react-oauth/google";
import { router } from "./routes";
import { MsalProviderWrapper } from "./providers/MsalProviderWrapper";
import { ErrorBoundary } from "./components/common/ErrorBoundary";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

export default function App() {
  return (
    <ErrorBoundary>
      <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
        <MsalProviderWrapper>
          <ReactFlowProvider>
            <RouterProvider router={router} />
          </ReactFlowProvider>
        </MsalProviderWrapper>
      </GoogleOAuthProvider>
    </ErrorBoundary>
  );
}
