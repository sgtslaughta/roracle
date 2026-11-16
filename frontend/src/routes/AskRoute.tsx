import { useParams } from "@tanstack/react-router";
import { AskPage } from "@/features/ask/AskPage";

export function AskRoute() {
  const { chatId } = useParams({ strict: false }) as { chatId?: string };
  return <AskPage key={chatId ?? "new"} chatId={chatId} />;
}
