/** Display title for a document: its title, else its filename, else a placeholder (never an empty heading). */
export const docTitle = (d: { title?: string | null; filename?: string | null }) => d.title?.trim() || d.filename?.trim() || "Untitled document";
