import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/newsreader";
import "@fontsource-variable/newsreader/wght-italic.css";
import "@fontsource-variable/manrope";
import "@fontsource/stix-two-text/400.css";
import "@fontsource/stix-two-text/400-italic.css";
import "./styles/globals.css";
import { applyTheme, getTheme } from "./lib/theme";

applyTheme(getTheme());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <h1>rOracle</h1>
  </StrictMode>,
);
