import type { Bbox } from "./types";

/** bare "collection" = a collection the reader may not see (non-owners) */
export type ChatScope = "all" | "open" | "collection" | `collection:${string}`;
export interface Chat { id: string; title: string; scope: ChatScope; model: string | null; pins: string[]; excludes: string[]; created_at: string; updated_at: string }
export interface ChatPage { items: Chat[]; next: string | null }
export interface Rating { value: 1 | -1; comment: string | null }
export interface Shares { users: string[]; groups: string[] }
export interface CitationOk {
  n: number;
  available: true;
  doc_id: string;
  version_id: string;
  version: number;
  chunk_id: string | null;
  title: string;
  page: number;
  bbox: Bbox | null;
  quote: string;
  authors: string[];
  year: number | null;
}
export interface CitationGone { n: number; available: false }
export type Citation = CitationOk | CitationGone;
export const isAvailable = (c: Citation): c is CitationOk => c.available !== false;
export interface RetrievedPassage { n: number; doc_id: string; version_id: string; version: number; chunk_id: string | null; title: string; page: number; bbox: Bbox | null; score: number; text: string; available?: true }
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
  rating?: Rating | null;
}
export interface ChatDetail extends Chat { messages: ChatMessage[]; is_owner: boolean /* ui */ }
export interface ModelsResponse { models: string[]; default: string } // ui: GET /chats/models (contract gap)
export interface SendMessageBody { content: string; viewer: Viewer; model?: string }
