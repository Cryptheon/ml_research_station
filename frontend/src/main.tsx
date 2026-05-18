import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../styles.css";
import "katex/dist/katex.min.css";
import { App } from "./components/App";
import { api } from "./api";

void api.boot();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
