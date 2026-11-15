import { Outlet } from "@tanstack/react-router";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ReaderHost } from "./ReaderHost";
import { useReaderStore } from "@/features/reader/useReaderStore";
import { cn } from "@/lib/utils";

export function Shell() {
  const { tabs, focus } = useReaderStore();
  const reading = tabs.length > 0;
  return (
    <TooltipProvider delayDuration={300}>
      <div className={cn("app", reading && "reading")} id="app" data-focus={focus}>
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
