import { ReactFlowProvider } from "@xyflow/react";
import { RouterProvider } from "react-router";
import { router } from "./routes";

export default function App() {
  return (
    <ReactFlowProvider>
      <RouterProvider router={router} />
    </ReactFlowProvider>
  );
}
