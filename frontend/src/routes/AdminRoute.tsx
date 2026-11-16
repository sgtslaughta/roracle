import { useParams } from "@tanstack/react-router";
import { ConnectorDetail } from "@/features/admin/ConnectorDetail";

export function ConnectorDetailRoute() {
  const { id } = useParams({ strict: false }) as { id: string };
  return <ConnectorDetail id={id} />;
}
