import { Link, Outlet } from "@tanstack/react-router";
import { ShieldAlert } from "lucide-react";
import { useMe } from "@/features/library/useMe";

/** `ready: false` entries are listed but not linked until their page exists. */
export const ADMIN_SECTIONS: { to: string; label: string; ready: boolean }[] = [
  { to: "/admin/connectors", label: "Connectors", ready: true },
  { to: "/admin/jobs", label: "Jobs", ready: true },
  { to: "/admin/review", label: "Review", ready: true },
  { to: "/admin/needs-title", label: "Needs title", ready: true },
  { to: "/admin/settings", label: "Settings", ready: false },
  { to: "/admin/tags", label: "Tags", ready: false },
  { to: "/admin/audit", label: "Audit", ready: false },
  { to: "/admin/users", label: "Users", ready: false },
  { to: "/admin/stats", label: "Stats", ready: false },
  { to: "/admin/health", label: "Health", ready: false },
];

/** Admin shell: non-admins get a notice and the child pages never mount, so no admin request is sent. */
export function AdminLayout() {
  const { data: me, isError } = useMe();
  if (isError) return <section className="view"><p role="alert">Your account couldn't be loaded. Reload to try again.</p></section>;
  if (!me) return <section className="view" aria-busy="true" />;
  if (!me.is_admin) {
    return (
      <section className="view">
        <div className="empty"><ShieldAlert aria-hidden /><h1>Admins only</h1><p>This section is for rOracle administrators.</p></div>
      </section>
    );
  }
  return (
    <section className="view">
      <h1>Admin</h1>
      <nav className="subnav" aria-label="Admin sections">
        {ADMIN_SECTIONS.map(({ to, label, ready }) =>
          ready ? (
            <Link key={to} to={to} activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>{label}</Link>
          ) : (
            <span key={to} aria-disabled="true" title="Coming soon">{label}</span>
          ),
        )}
      </nav>
      <Outlet />
    </section>
  );
}
