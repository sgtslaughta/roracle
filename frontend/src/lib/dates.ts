const D: Intl.DateTimeFormatOptions = { dateStyle: "medium" };
const DT: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" };

/** "Nov 15, 2026"; `empty` when the value is null/blank. */
export const fmtDate = (s: string | null | undefined, empty = "Never") => (s ? new Date(s).toLocaleDateString([], D) : empty);
/** "Nov 15, 2026, 9:30 AM"; `empty` when the value is null/blank. */
export const fmtDateTime = (s: string | null | undefined, empty = "Never") => (s ? new Date(s).toLocaleString([], DT) : empty);
