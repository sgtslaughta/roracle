/** Canned prompts. `OPEN_PAPER` are sent as-is (the backend answers them from the open paper: chat/intent.py);
 * `TEMPLATES` fill the composer with the caret on the first "…". ponytail: static; add a setting when asked. */
export const OPEN_PAPER = ["Summarize this paper", "Key findings", "Methods used", "Limitations", "Explain for a non-specialist"];
export const TEMPLATES = ["Summarize research on …", "Compare … and …", "What are the main findings about …"];
export const PLACEHOLDER = "…";
