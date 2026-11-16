import {
  createMemoryHistory, createRootRoute, createRoute, createRouter, lazyRouteComponent, redirect,
} from "@tanstack/react-router";
import type { ErrorRouteComponent, RouteComponent } from "@tanstack/react-router";
import { Shell } from "./Shell";
import { NotFound, Pending, RouteError } from "./RouterStates";
import { LibraryRoute } from "@/routes/LibraryRoute";
import { DocRoute } from "@/routes/DocRoute";
import { parseSearch, stringifySearch, validateLibrarySearch } from "@/lib/searchParams";

const AskRoute = lazyRouteComponent(() => import("@/routes/AskRoute"), "AskRoute");
const UploadsRoute = lazyRouteComponent(() => import("@/routes/UploadsRoute"), "UploadsRoute");
const DraftsPage = lazyRouteComponent(() => import("@/features/drafts/DraftsPage"), "DraftsPage");
const DraftPage = lazyRouteComponent(() => import("@/features/drafts/DraftPage"), "DraftPage");
const HandoffRoute = lazyRouteComponent(() => import("@/routes/HandoffRoute"), "HandoffRoute");
// Admin and low-traffic pages load on demand to keep the entry chunk small.
const ProposalsPage = lazyRouteComponent(() => import("@/features/proposals/ProposalsPage"), "ProposalsPage");
const NotificationsPage = lazyRouteComponent(() => import("@/features/notifications/NotificationsPage"), "NotificationsPage");
const SettingsRoute = lazyRouteComponent(() => import("@/routes/SettingsRoute"), "SettingsRoute");
const ConnectorDetailRoute = lazyRouteComponent(() => import("@/routes/AdminRoute"), "ConnectorDetailRoute");
const AdminLayout = lazyRouteComponent(() => import("@/features/admin/AdminLayout"), "AdminLayout");
const ConnectorsPage = lazyRouteComponent(() => import("@/features/admin/ConnectorsPage"), "ConnectorsPage");
const JobsPage = lazyRouteComponent(() => import("@/features/admin/JobsPage"), "JobsPage");
const ReviewPage = lazyRouteComponent(() => import("@/features/admin/ReviewPage"), "ReviewPage");
const SettingsPage = lazyRouteComponent(() => import("@/features/admin/SettingsPage"), "SettingsPage");
const TagsPage = lazyRouteComponent(() => import("@/features/admin/TagsPage"), "TagsPage");
const LabelsPage = lazyRouteComponent(() => import("@/features/admin/LabelsPage"), "LabelsPage");
const AuditPage = lazyRouteComponent(() => import("@/features/admin/AuditPage"), "AuditPage");
const UsersPage = lazyRouteComponent(() => import("@/features/admin/UsersPage"), "UsersPage");
const StatsPage = lazyRouteComponent(() => import("@/features/admin/StatsPage"), "StatsPage");
const HealthPage = lazyRouteComponent(() => import("@/features/admin/HealthPage"), "HealthPage");
const NeedsTitlePage = lazyRouteComponent(() => import("@/features/admin/NeedsTitlePage"), "NeedsTitlePage");

const rootRoute = createRootRoute({ component: Shell });
const r = <P extends string>(path: P, component: RouteComponent) =>
  createRoute({ getParentRoute: () => rootRoute, path, component });

const adminRoute = createRoute({ getParentRoute: () => rootRoute, path: "/admin", component: AdminLayout });
const a = (path: string, component: RouteComponent) => createRoute({ getParentRoute: () => adminRoute, path, component });
const adminTree = adminRoute.addChildren([
  createRoute({ getParentRoute: () => adminRoute, path: "/", beforeLoad: () => { throw redirect({ to: "/admin/connectors" }); } }),
  a("connectors", ConnectorsPage),
  a("connectors/$id", ConnectorDetailRoute),
  a("jobs", JobsPage),
  a("review", ReviewPage),
  a("needs-title", NeedsTitlePage),
  a("settings", SettingsPage),
  a("tags", TagsPage),
  a("labels", LabelsPage),
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
  r("/drafts", DraftsPage),
  r("/drafts/$id", DraftPage),
  r("/d/$docId", DocRoute),
  r("/d/$docId/v/$version", DocRoute),
  r("/settings", SettingsRoute),
  r("/h/$code", HandoffRoute),
  adminTree,
]);

export function createAppRouter(initialEntries?: string[]) {
  return createRouter({
    routeTree,
    defaultNotFoundComponent: NotFound,
    defaultErrorComponent: RouteError as ErrorRouteComponent,
    defaultPendingComponent: Pending,
    defaultPendingMs: 300,
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
