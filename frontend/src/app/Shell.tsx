import { Outlet } from "@tanstack/react-router";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ReaderHost } from "./ReaderHost";

export function Shell() {
  return (
    <TooltipProvider delayDuration={300}>
      <div className="app" id="app">
        <TopBar />
        <Sidebar />
        <main>
          <Outlet />
        </main>
        <ReaderHost />
      </div>
      <Toaster />
    </TooltipProvider>
  );
}
