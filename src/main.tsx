import { createRoot } from "react-dom/client";
import "./styles/index.css";
import "@xyflow/react/dist/style.css";

if (window.location.pathname === "/auth/callback") {
  void import("./msal-redirect-bridge");
} else {
  void import("./App.tsx").then(({ default: App }) => {
    createRoot(document.getElementById("root")!).render(<App />);
  });
}
