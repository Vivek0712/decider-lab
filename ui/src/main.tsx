import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { captureTokenFromUrl } from "@/lib/api";
import { App } from "@/app/App";
import "./index.css";

captureTokenFromUrl();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
