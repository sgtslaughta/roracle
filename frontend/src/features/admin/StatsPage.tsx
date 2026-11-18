import { humanBytes } from "@/lib/bytes";
import { useStats } from "./dataApi";
import { humanize } from "./StatusPill";

export function StatsPage() {
  const { data: s, isError } = useStats();
  if (isError) return <p role="alert">Stats couldn't be loaded. Reload to try again.</p>;
  if (!s) return <div aria-busy="true" />;
  const card = (label: string, value: string | number) => <div className="card stat" key={label}><span>{label}</span><b>{typeof value === "number" ? value.toLocaleString("en") : value}</b></div>;
  return (
    <div className="admin-page">
      <h2>Stats</h2>
      <div className="sumgrid" aria-label="Totals">
        {card("Documents live", s.documents.live)}{card("Documents deleted", s.documents.deleted)}
        {card("Blobs", s.blobs.count)}{card("Blob storage", humanBytes(s.blobs.bytes))}
        {card("Users", s.users)}{card("Chats", s.chats)}{card("Connectors", s.connectors)}
      </div>
      <h3>Versions by stage</h3>
      <div className="sumgrid" aria-label="Versions by stage">
        {Object.entries(s.versions).sort().map(([k, n]) => card(humanize(k), n))}
      </div>
      <h2>Top uploaders</h2>
      {!s.top_uploaders.length && <p className="sub">No uploads yet.</p>}
      {s.top_uploaders.length > 0 && (
        <div className="tok-wrap"><table className="agent-table admin-table">
          <thead><tr><th>User</th><th>Documents</th><th>Storage</th></tr></thead>
          <tbody>{s.top_uploaders.slice(0, 10).map((u) => <tr key={u.user_id}><td>{u.username}</td><td>{u.documents}</td><td>{humanBytes(u.bytes)}</td></tr>)}</tbody>
        </table></div>
      )}
    </div>
  );
}
