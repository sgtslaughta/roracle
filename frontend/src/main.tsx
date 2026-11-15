import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/newsreader";
import "@fontsource-variable/newsreader/wght-italic.css";
import "@fontsource-variable/manrope";
import "@fontsource/stix-two-text/400.css";
import "@fontsource/stix-two-text/400-italic.css";
import "./styles/globals.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { applyTheme, getTheme } from "./lib/theme";
import { createAppRouter } from "./app/router";
import { AuthGate } from "./auth/AuthGate";

const MOCK = import.meta.env.VITE_MOCK === "1";

async function boot() {
  applyTheme(getTheme());
  if (window.location.pathname === "/auth/silent") {
    const { createUserManager } = await import("./auth/oidc");
    await createUserManager().signinSilentCallback();
    return;
  }
  if (MOCK) {
    const { worker } = await import("./mocks/browser");
    await worker.start({ onUnhandledFrame: "bypass", quiet: true });
  }
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 15_000 } } });
  const router = createAppRouter();
  const app = (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
  createRoot(document.getElementById("root")!).render(<StrictMode>{MOCK ? app : <AuthGate>{app}</AuthGate>}</StrictMode>);
}

void boot();
