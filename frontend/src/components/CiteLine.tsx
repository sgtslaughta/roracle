import { citeLine } from "@/lib/cite";

/** "Authors. <i>Venue</i>, Year." with absent parts omitted (no "null", no dangling commas). */
export function CiteLine({ authors, venue, year, org }: { authors: string[]; venue?: string | null; year: number | null; org?: string }) {
  const a = authors.join(", ");
  return (
    <p className="citeline trunc" title={citeLine(authors, venue, year) + (org ? ` ${org}.` : "")}>
      {a}{a && (venue || year != null) ? ". " : ""}{venue && <i>{venue}</i>}{venue && year != null ? ", " : ""}{year ?? ""}{a || venue || year != null ? "." : ""}{org ? ` ${org}.` : ""}
    </p>
  );
}
