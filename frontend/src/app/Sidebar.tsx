import { Link } from "@tanstack/react-router";
import { Folder } from "lucide-react";
import { OverflowList } from "@/components/OverflowList";
import { useCollections } from "@/features/library/api";
import { useUploadAttention } from "@/features/uploads/api";
import { NAV } from "./nav";

export function Sidebar() {
  const { data: collections } = useCollections();
  const attention = useUploadAttention();
  return (
    <aside className="side">
      <nav className="nav" aria-label="Main">
        {NAV.map(({ to, label, icon: Icon }) => (
          <Link key={to} to={to} activeProps={{ "aria-current": "page" }} inactiveProps={{ "aria-current": undefined }}>
            <Icon aria-hidden />
            <span>{label}</span>
            {to === "/uploads" && attention > 0 && <b className="badge" aria-label={`${attention} need attention`}>{attention}</b>}
          </Link>
        ))}
      </nav>
      {!!collections?.length && (
        <div className="sgroup">
          <h5>Collections</h5>
          <nav className="nav" aria-label="Collections">
            <OverflowList
              items={collections} max={6} layout="stack" menuLabel="More collections" getLabel={(c) => c.name} moreLabel={(k) => `+${k} more`}
              renderItem={(c) => (
                <Link key={c.id} to="/library" search={{ collection: [c.name] }} title={c.name}>
                  <Folder aria-hidden />
                  <span>{c.name}</span>
                </Link>
              )}
            />
          </nav>
        </div>
      )}
    </aside>
  );
}
