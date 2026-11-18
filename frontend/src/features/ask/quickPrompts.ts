/** Canned prompts. `OPEN_PAPER` are sent as-is (the backend answers them from the open paper: chat/intent.py);
 * `TEMPLATES` fill the composer with the first [slot] selected. ponytail: static; add a setting when asked. */
export const OPEN_PAPER = ["Summarize this paper", "Key findings", "Methods used", "Limitations", "Explain for a non-specialist"];
export const TEMPLATES = ["Summarize research on [topic]", "Compare [A] and [B]", "What are the main findings about [topic]"];
/** The first bracketed slot in a template. */
export const SLOT = /\[[^\]]+\]/;
