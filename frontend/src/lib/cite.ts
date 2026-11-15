const surname = (a: string) => a.trim().split(/\s+/).at(-1) ?? a;

/** "Ito 2023", "Chen & Aldana 2021", "Chen et al. 2020"; the year is omitted when unknown. */
export function shortCite(authors: string[], year: number | null): string {
  const y = year == null ? "" : ` ${year}`;
  if (!authors.length) return y.trim();
  if (authors.length === 1) return `${authors[0].includes(" ") ? surname(authors[0]) : authors[0]}${y}`;
  if (authors.length === 2) return `${surname(authors[0])} & ${surname(authors[1])}${y}`;
  return `${surname(authors[0])} et al.${y}`;
}

/** "Authors. Venue, Year." with every missing part left out (never prints "null"). */
export function citeLine(authors: string[], venue: string | null | undefined, year: number | null): string {
  const tail = [venue, year].filter((x) => x != null && x !== "").join(", ");
  return [authors.join(", "), tail].filter(Boolean).join(". ") + ".";
}
