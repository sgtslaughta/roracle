import { useParams } from "@tanstack/react-router";
import { EvalSetPage } from "@/features/admin/EvalSetPage";
import { ConnectorDetail } from "@/features/admin/ConnectorDetail";

export function ConnectorDetailRoute() {
  const { id } = useParams({ strict: false }) as { id: string };
  return <ConnectorDetail id={id} />;
}

export function EvalSetRoute() {
  const { id } = useParams({ strict: false }) as { id: string };
  return <EvalSetPage id={id} />;
}
