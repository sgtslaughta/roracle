/** Typing shaping shared by the top-bar search and the palette (task 10.12): settle for 300 ms, 3+ characters. */
export const SEARCH_DEBOUNCE_MS = 300;
export const MIN_SEARCH_CHARS = 3;
/** Empty (back to browse) or long enough to search. */
export const searchable = (q: string) => q === "" || q.length >= MIN_SEARCH_CHARS;
