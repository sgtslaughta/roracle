import {
  createMemoryHistory, createRootRoute, createRoute, createRouter, redirect,
} from "@tanstack/react-router";
import { Shell } from "./Shell";
import { LibraryRoute } from "@/routes/LibraryRoute";
import { DraftsRoute, ProposalsRoute } from "@/routes/placeholders";
import { AskRoute } from "@/routes/AskRoute";
import { UploadsRoute } from "@/routes/UploadsRoute";
import { DocRoute } from "@/routes/DocRoute";
import { SettingsRoute } from "@/routes/SettingsRoute";
import { HandoffRoute } from "@/routes/HandoffRoute";
import { parseSearch, stringifySearch, validateLibrarySearch } from "@/lib/searchParams";

const rootRoute = createRootRoute({ component: Shell });
const r = <P extends string>(path: P, component: () => React.ReactNode) =>
  createRoute({ getParentRoute: () => rootRoute, path, component });

const routeTree = rootRoute.addChildren([
  createRoute({ getParentRoute: () => rootRoute, path: "/", beforeLoad: () => { throw redirect({ to: "/library" }); } }),
  createRoute({ getParentRoute: () => rootRoute, path: "/library", component: LibraryRoute, validateSearch: validateLibrarySearch }),
  r("/ask", AskRoute),
  r("/ask/$chatId", AskRoute),
  r("/uploads", UploadsRoute),
  r("/proposals", ProposalsRoute),
  r("/drafts", DraftsRoute),
  r("/d/$docId", DocRoute),
  r("/d/$docId/v/$version", DocRoute),
  r("/settings", SettingsRoute),
  r("/h/$code", HandoffRoute),
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
