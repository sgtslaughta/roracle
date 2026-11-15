import { Toaster as Sonner } from "sonner";

export const Toaster = () => (
  <Sonner
    position="bottom-right"
    toastOptions={{ style: { background: "var(--ink)", color: "var(--bg)", border: "0", fontSize: "13.5px" } }}
  />
);
