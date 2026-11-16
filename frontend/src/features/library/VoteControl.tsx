import { Clock, GitCompare, ThumbsDown, ThumbsUp, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import type { DownvoteReason, Votes } from "@/api/types";
import { cn } from "@/lib/utils";
import { useVote } from "./api";

const REASONS: { id: DownvoteReason; label: string; icon: typeof Clock }[] = [
  { id: "outdated", label: "Outdated", icon: Clock },
  { id: "wrong", label: "Wrong or misleading", icon: TriangleAlert },
  { id: "low_quality", label: "Low quality", icon: ThumbsDown },
  { id: "superseded", label: "Superseded by a newer document", icon: GitCompare },
];

/** Up/down vote with a reason menu on downvote. `candidates` = documents that could supersede this one. */
export function VoteControl({ docId, votes, row, candidates = [] }: { docId: string; votes: Votes; row?: boolean; candidates?: { id: string; title: string }[] }) {
  const vote = useVote(docId);
  const [menu, setMenu] = useState<"reasons" | "pick" | null>(null);
  const [target, setTarget] = useState(candidates[0]?.id ?? "");
  const score = votes.up - votes.down;
  const reasons = candidates.length ? REASONS : REASONS.filter((r) => r.id !== "superseded");

  /** `thanks` is toasted only once the server accepted the vote. */
  const send = (value: 1 | -1 | 0, reason?: DownvoteReason, superseded_by?: string, thanks?: string) => {
    vote.mutate({ value, reason, superseded_by }, { onSuccess: () => thanks && toast(thanks) });
    setMenu(null);
  };

  return (
    <div className={cn("vote", row && "row")}>
      <button className="up" aria-pressed={votes.mine > 0} aria-label="Upvote" title="Upvote: useful" onClick={() => send(votes.mine > 0 ? 0 : 1)}>
        <ThumbsUp aria-hidden fill={votes.mine > 0 ? "currentColor" : "none"} />
      </button>
      <span className={cn("score", score > 0 && "pos", score < 0 && "neg")} title={`${votes.up} useful, ${votes.down} not useful`}>{score}</span>
      <Popover open={menu !== null} onOpenChange={(o) => !o && setMenu(null)}>
        <PopoverAnchor asChild>
          <button
            className="down" aria-pressed={votes.mine < 0} aria-label="Downvote" title="Downvote: not useful"
            onClick={() => (votes.mine < 0 ? send(0) : setMenu("reasons"))}
          >
            <ThumbsDown aria-hidden fill={votes.mine < 0 ? "currentColor" : "none"} />
          </button>
        </PopoverAnchor>
        <PopoverContent className="reasons" role="menu">
          {menu === "pick" ? (
            <>
              <p>Which document replaces it?</p>
              <select className="sel" aria-label="Replacing document" value={target} onChange={(e) => setTarget(e.target.value)}>
                {candidates.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
              </select>
              <button role="menuitem" onClick={() => send(-1, "superseded", target, "Marked “Superseded”. Thanks.")}>Confirm</button>
            </>
          ) : (
            <>
              <p>What's wrong with it? The owner sees your reason.</p>
              {reasons.map(({ id, label, icon: Icon }) => (
                <button
                  key={id} role="menuitem"
                  onClick={() => {
                    if (id === "superseded") { setTarget(candidates[0]?.id ?? ""); setMenu("pick"); return; }
                    send(-1, id, undefined, `Marked “${label}”. Thanks.`);
                  }}
                >
                  <Icon aria-hidden />{label}
                </button>
              ))}
            </>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}
