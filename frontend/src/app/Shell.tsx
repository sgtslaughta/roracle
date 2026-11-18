import { Outlet } from "@tanstack/react-router";
import { Toaster } from "@/components/ui/sonner";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ReaderHost } from "./ReaderHost";
import { useReaderStore } from "@/features/reader/useReaderStore";
import { cn } from "@/lib/utils";
import { CommandPalette } from "@/features/palette/CommandPalette";
import { useGlobalKeys } from "@/lib/keys";
import { HelpHost } from "@/features/help/HelpHost";
import { useHelpHash } from "@/features/help/useHelpHash";

export function Shell() {
  const { tabs, focus } = useReaderStore();
  const reading = tabs.length > 0;
  useGlobalKeys();
  useHelpHash();
  return (
    <>
      <div className={cn("app", reading && "reading")} id="app" data-focus={focus}>
        <TopBar />
        <Sidebar />
        <main>
          <Outlet />
        </main>
        <ReaderHost />
      </div>
      <CommandPalette />
      <HelpHost />
      <Toaster />
    </>
  );
}
