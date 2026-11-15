import { Inbox, MessagesSquare, PenLine } from "lucide-react";
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

export const AskRoute = () => (
  <Placeholder title="Ask" sub="Questions answered from the documents you can see, with every passage cited." icon={<MessagesSquare />}>
    Ask a question about the library. Documents open in the reader count as context, and every answer cites the passages it used. This arrives in the next release.
  </Placeholder>
);

export const ProposalsRoute = () => (
  <Placeholder title="Agent proposals" sub="Notes and evidence your agents want to add. Nothing is published until you approve it." icon={<Inbox />}>
    No proposals waiting. When an agent suggests a note or attaches evidence, it shows up here for you to approve.
  </Placeholder>
);

export const DraftsRoute = () => (
  <Placeholder title="Drafts" sub="Papers you're writing with an agent. Each draft is checked against its writing profile before you can submit it." icon={<PenLine />}>
    No drafts yet. Drafts you start with an agent appear here with their checks.
  </Placeholder>
);
