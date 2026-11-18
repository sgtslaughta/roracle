export interface Step { id: string; title: string; hint: string; topic: string; section?: string }

export const BASICS: Step[] = [
  { id: "search", title: "Search the library", hint: "Try a phrase in quotes.", topic: "library-search" },
  { id: "ask", title: "Open a paper and ask about it", hint: "Ask about this, in the reader.", topic: "chat" },
  { id: "upload", title: "Upload a paper", hint: "PDF or Word. Duplicates are caught.", topic: "uploads" },
  { id: "collect", title: "Save a collection", hint: "Group papers for a project.", topic: "collections-sharing" },
];

export const AGENTS: Step[] = [
  { id: "token", title: "Create an access token", hint: "Your agent signs in with it.", topic: "agents-mcp-setup", section: "create-a-token" },
  { id: "mcp", title: "Connect an agent with MCP", hint: "Paste the config into your client.", topic: "agents-mcp-setup" },
  { id: "handoff", title: "Hand a chat to an agent", hint: "Continue in agent, from any chat.", topic: "handoffs-inbox" },
];
