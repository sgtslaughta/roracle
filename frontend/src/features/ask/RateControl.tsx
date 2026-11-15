import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { useRateMessage } from "./api";

const THANKS = "Thanks. Ratings help admins tune answers.";

export function RateControl({ chatId, messageId, rating }: { chatId: string; messageId: string; rating: 1 | -1 | null | undefined }) {
  const rate = useRateMessage(chatId);
  const [why, setWhy] = useState<string | null>(null); // null = popover closed
  const send = (value: 1 | -1, comment?: string) =>
    rate.mutate({ messageId, value, comment }, { onSuccess: () => toast(THANKS), onError: () => toast("The rating couldn't be saved. Try again.") });
  return (
    <>
      <Button variant="ghost" aria-label="Helpful" aria-pressed={rating === 1} onClick={() => rating !== 1 && send(1)}><ThumbsUp aria-hidden /></Button>
      <Popover open={why !== null} onOpenChange={(o) => !o && setWhy(null)}>
        <PopoverAnchor asChild>
          <Button variant="ghost" aria-label="Not helpful" aria-pressed={rating === -1} onClick={() => { if (rating !== -1) { send(-1); setWhy(""); } }}><ThumbsDown aria-hidden /></Button>
        </PopoverAnchor>
        <PopoverContent className="rate-pop" aria-label="What went wrong?">
          <label htmlFor={`why-${messageId}`}>What went wrong? (optional)</label>
          <textarea id={`why-${messageId}`} rows={3} value={why ?? ""} onChange={(e) => setWhy(e.target.value)} />
          <Button variant="primary" disabled={!why?.trim()} onClick={() => { send(-1, why!.trim()); setWhy(null); }}>Send</Button>
        </PopoverContent>
      </Popover>
    </>
  );
}
