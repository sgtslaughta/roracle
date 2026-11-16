import { PenLine } from "lucide-react";
import type { ReactNode } from "react";

function Placeholder({ title, sub, icon, children }: { title: string; sub: string; icon: ReactNode; children: string }) {
  return (
    <section className="view">
      <div className="uhead-row">
        <div>
          <h1>{title}</h1>
          <p className="sub">{sub}</p>
        </div>
      </div>
      <div className="empty">
        {icon}
        <p>{children}</p>
      </div>
    </section>
  );
}

export const DraftsRoute = () => (
  <Placeholder title="Drafts" sub="Papers you're writing with an agent. Each draft is checked against its writing profile before you can submit it." icon={<PenLine />}>
    No drafts yet. Drafts you start with an agent appear here with their checks.
  </Placeholder>
);
