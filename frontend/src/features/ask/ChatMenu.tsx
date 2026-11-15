import { Download, Pencil, Share2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import type { ChatDetail } from "@/api/chat";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { exportChat, usePatchChat } from "./api";
import { ShareDialog } from "./ShareDialog";

const slug = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "chat";

function Rename({ chat }: { chat: ChatDetail }) {
  const patch = usePatchChat(chat.id);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(chat.title);
  const save = () => {
    const t = name.trim();
    if (t && t !== chat.title) patch.mutate({ title: t }, { onError: () => toast("The chat couldn't be renamed. Try again.") });
    setOpen(false);
  };
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) setName(chat.title); }}>
      <PopoverTrigger asChild><Button variant="ghost"><Pencil aria-hidden />Rename</Button></PopoverTrigger>
      <PopoverContent align="end" className="rename-pop" aria-label="Rename chat">
        <input aria-label="Chat title" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && save()} />
      </PopoverContent>
    </Popover>
  );
}

export function ChatMenu({ chat }: { chat: ChatDetail }) {
  const [sharing, setSharing] = useState(false);
  const download = async () => {
    try {
      const url = URL.createObjectURL(await exportChat(chat.id));
      const a = document.createElement("a");
      a.href = url; a.download = `${slug(chat.title)}.md`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch { toast("The chat couldn't be exported. Try again."); }
  };
  return (
    <>
      {chat.is_owner && <Button variant="ghost" onClick={() => setSharing(true)}><Share2 aria-hidden />Share</Button>}
      <Button variant="ghost" onClick={() => void download()}><Download aria-hidden />Export</Button>
      {chat.is_owner && <Rename chat={chat} />}
      {chat.is_owner && sharing && <ShareDialog chatId={chat.id} open={sharing} onOpenChange={setSharing} />}
    </>
  );
}
