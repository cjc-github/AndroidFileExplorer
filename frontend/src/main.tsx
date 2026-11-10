import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";

import { router } from "./router";

import "./styles.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root not found");
}

createRoot(container).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
