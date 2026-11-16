import { Link, Outlet } from "@tanstack/react-router";
import { ShieldAlert } from "lucide-react";
import { useMe } from "@/features/library/useMe";

export const ADMIN_SECTIONS: { to: string; label: string }[] = [
  { to: "/admin/connectors", label: "Connectors" },
  { to: "/admin/jobs", label: "Jobs" },
  { to: "/admin/review", label: "Review" },
  { to: "/admin/needs-title", label: "Needs title" },
  { to: "/admin/settings", label: "Settings" },
  { to: "/admin/tags", label: "Tags" },
  { to: "/admin/audit", label: "Audit" },
  { to: "/admin/users", label: "Users" },
  { to: "/admin/stats", label: "Stats" },
  { to: "/admin/health", label: "Health" },
];

/** Admin shell: non-admins get a notice and the child pages never mount, so no admin request is sent. */
export function AdminLayout() {
  const { data: me, isError } = useMe();
  if (isError) return <section className="view"><p role="alert">Your account couldn't be loaded. Reload to try again.</p></section>;
  if (!me) return <section className="view" aria-busy="true" />;
  if (!me.is_admin) {
    return (
      <section className="view">
        <div className="empty"><ShieldAlert aria-hidden /><h1>Admins only</h1><p>This section is for rOracle administrators.</p><Link to="/library">Back to Library</Link></div>
      </section>
    );
  }
  return (
    <section className="view">
      <h1>Admin</h1>
      <nav className="subnav" aria-label="Admin sections">
        {ADMIN_SECTIONS.map(({ to, label }) => (
          <Link key={to} to={to} activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>{label}</Link>
        ))}
      </nav>
      <Outlet />
    </section>
  );
}
