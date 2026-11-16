import { Link } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";

const CHUNK_ERR = /dynamically imported module|Importing a module script failed|error loading dynamically|Failed to fetch dynamically/i;

export function NotFound() {
  return (
    <section className="view">
      <div className="empty">
        <h1>Page not found</h1>
        <p>That page does not exist or was moved.</p>
        <p><Link to="/library">Back to Library</Link></p>
      </div>
    </section>
  );
}

export function RouteError({ error, reset }: { error: Error; reset?: () => void }) {
  const chunk = CHUNK_ERR.test(error?.message ?? "");
  return (
    <section className="view">
      <div className="empty" role="alert">
        <h1>{chunk ? "Could not load this page" : "Something went wrong"}</h1>
        <p>{chunk ? "Part of the app failed to download. Check your connection, or the app may have been updated. Reload to try again." : error?.message || "An unexpected error occurred."}</p>
        <p><Button type="button" onClick={() => (chunk || !reset ? location.reload() : reset())}>{chunk || !reset ? "Reload" : "Retry"}</Button></p>
      </div>
    </section>
  );
}

export function Pending() {
  return <div className="empty" role="status" aria-busy="true" style={{ border: 0 }}>Loading…</div>;
}
