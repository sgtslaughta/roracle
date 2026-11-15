import { useParams } from "@tanstack/react-router";
import { HandoffLanding } from "@/features/agents/HandoffLanding";

export function HandoffRoute() {
  const { code } = useParams({ from: "/h/$code" });
  return <section className="view"><HandoffLanding key={code} code={code} /></section>;
}
