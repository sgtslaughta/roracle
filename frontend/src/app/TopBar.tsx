import { Bell, Command, Moon, Search, Sun, SunMoon } from "lucide-react";
import { toast } from "sonner";
import { cycleTheme, useTheme } from "@/lib/theme";
import { openPalette } from "@/features/palette/usePalette";
import { GlobalSearch } from "./GlobalSearch";

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
        <button className="icon-btn" aria-label="Notifications">
          <Bell aria-hidden />
        </button>
        <div className="avatar" title="Signed in">
          R
        </div>
      </div>
    </header>
  );
}
