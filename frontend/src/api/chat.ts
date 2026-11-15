import type { Bbox } from "./types";

export type ChatScope = "all" | "open" | `collection:${string}`;
export interface Chat { id: string; title: string; scope: ChatScope; model: string; pins: string[]; excludes: string[]; created_at: string; updated_at: string }
export interface ChatSummary { id: string; title: string; updated_at: string }
export interface CitationOk {
  n: number;
  available?: true;
  doc_id: string;
  version_id: string;
  version?: number; // ui: version number for openDoc (not in the P4 plan)
  chunk_id: string;
  title: string;
  page: number;
  bbox: Bbox | null;
  quote: string;
}
export interface CitationGone { n: number; available: false }
export type Citation = CitationOk | CitationGone;
export const isAvailable = (c: Citation): c is CitationOk => c.available !== false;
// ui: P4 'retrieved' jsonb shape is unspecified; this is the UI's assumption
export interface RetrievedPassage { n?: number; doc_id: string; version_id: string; chunk_id: string; title: string; page: number; score: number; text: string }
export interface ViewerTab { doc_id: string; version_id: string | null; page: number }
export interface Viewer { tabs: ViewerTab[]; ignored: string[]; selection: { doc_id: string; page: number; text: string } | null }
export type MessageStatus = "complete" | "interrupted" | "failed";
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  viewer?: Viewer | null;
  citations: Citation[];
  retrieved: RetrievedPassage[];
  status: MessageStatus;
  model: string | null;
  created_at: string;
  rating?: 1 | -1 | null; // ui: the caller's own rating, not in the P4 plan
}
export interface ChatDetail extends Chat { messages: ChatMessage[]; is_owner: boolean /* ui */ }
export interface ModelsResponse { models: string[]; default: string } // ui: GET /chats/models (contract gap)
export interface SendMessageBody { content: string; viewer: Viewer; model?: string }
