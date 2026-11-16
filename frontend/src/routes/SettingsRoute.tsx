import { Bot, KeyRound } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { HandoffsPanel } from "@/features/agents/HandoffsPanel";
import { TokensPanel } from "@/features/agents/TokensPanel";

export function SettingsRoute() {
  return (
    <section className="view">
      <h1>Settings</h1>
      <Tabs defaultValue="tokens">
        <TabsList aria-label="Settings sections">
          <TabsTrigger value="tokens"><KeyRound aria-hidden />Access tokens</TabsTrigger>
          <TabsTrigger value="handoffs"><Bot aria-hidden />Handoffs</TabsTrigger>
        </TabsList>
        <TabsContent value="tokens"><TokensPanel /></TabsContent>
        <TabsContent value="handoffs"><HandoffsPanel /></TabsContent>
      </Tabs>
    </section>
  );
}
