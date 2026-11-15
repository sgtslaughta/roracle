import { Link } from "@tanstack/react-router";
import { NAV } from "./nav";

export function Sidebar() {
  return (
    <aside className="side">
      <nav className="nav" aria-label="Main">
        {NAV.map(({ to, label, icon: Icon }) => (
          <Link key={to} to={to} activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>
            <Icon aria-hidden />
            <span>{label}</span>
          </Link>
        ))}
      </nav>
    </aside>
  );
}
