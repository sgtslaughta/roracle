import { Link } from "@tanstack/react-router";
import { Command, Moon, Search, Settings, Shield, Sun, SunMoon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { cycleTheme, useTheme } from "@/lib/theme";
import { openPalette } from "@/features/palette/usePalette";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useMe } from "@/features/library/useMe";
import { GlobalSearch } from "./GlobalSearch";
import { Bell } from "@/features/notifications/Bell";

const THEME_ICON = { system: SunMoon, light: Sun, dark: Moon } as const;

export function TopBar() {
  const theme = useTheme();
  const ThemeIcon = THEME_ICON[theme];
  return (
    <header className="top">
      <div className="brand">
        <img className="logo" src="/logo.png" alt="" />
        <span>rOracle</span>
      </div>
      <GlobalSearch icon={<Search aria-hidden />} />
      <div className="tact">
        <button className="icon-btn" aria-label="Command menu" title="Command menu (Ctrl K)" onClick={openPalette}>
          <Command aria-hidden />
        </button>
        <button
          className="icon-btn keep"
          aria-label="Switch theme"
          title={`Theme: ${theme}`}
          onClick={() => {
            const next = cycleTheme();
            toast(`Theme: ${next === "system" ? "match your system" : next}`);
          }}
        >
          <ThemeIcon aria-hidden />
        </button>
        <Bell />
        <AccountMenu />
      </div>
    </header>
  );
}

/** Avatar popover: a small nav list (links, not an ARIA menu). Admin entry only for admins. */
function AccountMenu() {
  const { data: me } = useMe();
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button className="avatar" aria-label="Account menu" title={me ? `Signed in as ${me.username}` : "Signed in"}>
          {me?.username.charAt(0).toUpperCase() || "R"}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="chipmenu">
        <nav aria-label="Account">
          <ul className="menu-list">
            <li><Link to="/settings" className="menu-link" onClick={() => setOpen(false)}><Settings aria-hidden />Settings</Link></li>
            {me?.is_admin && <li><Link to="/admin" className="menu-link" onClick={() => setOpen(false)}><Shield aria-hidden />Admin</Link></li>}
          </ul>
        </nav>
      </PopoverContent>
    </Popover>
  );
}
