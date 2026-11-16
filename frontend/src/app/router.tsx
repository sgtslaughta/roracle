import {
  createMemoryHistory, createRootRoute, createRoute, createRouter, redirect,
} from "@tanstack/react-router";
import { Shell } from "./Shell";
import { LibraryRoute } from "@/routes/LibraryRoute";
import { DraftsRoute } from "@/routes/placeholders";
import { ProposalsPage } from "@/features/proposals/ProposalsPage";
import { NotificationsPage } from "@/features/notifications/NotificationsPage";
import { AskRoute } from "@/routes/AskRoute";
import { UploadsRoute } from "@/routes/UploadsRoute";
import { DocRoute } from "@/routes/DocRoute";
import { SettingsRoute } from "@/routes/SettingsRoute";
import { HandoffRoute } from "@/routes/HandoffRoute";
import { ConnectorDetailRoute } from "@/routes/AdminRoute";
import { AdminLayout } from "@/features/admin/AdminLayout";
import { ConnectorsPage } from "@/features/admin/ConnectorsPage";
import { JobsPage } from "@/features/admin/JobsPage";
import { ReviewPage } from "@/features/admin/ReviewPage";
import { SettingsPage } from "@/features/admin/SettingsPage";
import { TagsPage } from "@/features/admin/TagsPage";
import { AuditPage } from "@/features/admin/AuditPage";
import { UsersPage } from "@/features/admin/UsersPage";
import { StatsPage } from "@/features/admin/StatsPage";
import { HealthPage } from "@/features/admin/HealthPage";
import { NeedsTitlePage } from "@/features/admin/NeedsTitlePage";
import { parseSearch, stringifySearch, validateLibrarySearch } from "@/lib/searchParams";

const rootRoute = createRootRoute({ component: Shell });
const r = <P extends string>(path: P, component: () => React.ReactNode) =>
  createRoute({ getParentRoute: () => rootRoute, path, component });

const adminRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin", component: AdminLayout });
const a = (path: string, component: () => React.ReactNode) => createRoute({ getParentRoute: () => adminRoute, path, component });
const adminTree = adminRoute.addChildren([
  createRoute({ getParentRoute: () => adminRoute, path: "/", beforeLoad: () => { throw redirect({ to: "/admin/connectors" }); } }),
  a("connectors", ConnectorsPage),
  a("connectors/$id", ConnectorDetailRoute),
  a("jobs", JobsPage),
  a("review", ReviewPage),
  a("needs-title", NeedsTitlePage),
  a("settings", SettingsPage),
  a("tags", TagsPage),
  a("audit", AuditPage),
  a("users", UsersPage),
  a("stats", StatsPage),
  a("health", HealthPage),
]);

const routeTree = rootRoute.addChildren([
  createRoute({ getParentRoute: () => rootRoute, path: "/", beforeLoad: () => { throw redirect({ to: "/library" }); } }),
  createRoute({ getParentRoute: () => rootRoute, path: "/library", component: LibraryRoute, validateSearch: validateLibrarySearch }),
  r("/ask", AskRoute),
  r("/ask/$chatId", AskRoute),
  r("/uploads", UploadsRoute),
  r("/proposals", ProposalsPage),
  r("/notifications", NotificationsPage),
  r("/drafts", DraftsRoute),
  r("/d/$docId", DocRoute),
  r("/d/$docId/v/$version", DocRoute),
  r("/settings", SettingsRoute),
  r("/h/$code", HandoffRoute),
  adminTree,
]);

export function createAppRouter(initialEntries?: string[]) {
  return createRouter({
    routeTree,
    parseSearch,
    stringifySearch,
    ...(initialEntries ? { history: createMemoryHistory({ initialEntries }) } : {}),
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
