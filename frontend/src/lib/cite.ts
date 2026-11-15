const surname = (a: string) => a.trim().split(/\s+/).at(-1) ?? a;

/** "Ito 2023", "Chen & Aldana 2021", "Chen et al. 2020" */
export function shortCite(authors: string[], year: number): string {
  if (!authors.length) return String(year);
  if (authors.length === 1) return `${authors[0].includes(" ") ? surname(authors[0]) : authors[0]} ${year}`;
  if (authors.length === 2) return `${surname(authors[0])} & ${surname(authors[1])} ${year}`;
  return `${surname(authors[0])} et al. ${year}`;
}
