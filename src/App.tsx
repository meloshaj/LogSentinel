import { ReactFlowProvider } from "@xyflow/react";
import { RouterProvider } from "react-router";
import { GoogleOAuthProvider } from "@react-oauth/google";
import { router } from "./routes";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

export default function App() {
  return (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <ReactFlowProvider>
        <RouterProvider router={router} />
      </ReactFlowProvider>
    </GoogleOAuthProvider>
  );
}
