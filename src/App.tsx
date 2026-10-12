import { RouterProvider } from "react-router";
import { router } from "./routes";
import { MsalProviderWrapper } from "./providers/MsalProviderWrapper";
import { ErrorBoundary } from "./components/common/ErrorBoundary";

export default function App() {
  return (
    <ErrorBoundary>
      <MsalProviderWrapper>
        <RouterProvider router={router} />
      </MsalProviderWrapper>
    </ErrorBoundary>
  );
}
