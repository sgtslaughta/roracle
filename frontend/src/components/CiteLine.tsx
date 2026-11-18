import { citeLine } from "@/lib/cite";

/** "Authors. <i>Venue</i>, Year." with absent parts omitted (no "null", no dangling commas). */
export function CiteLine({ authors, venue, year, org }: { authors: string[]; venue?: string | null; year: number | null; org?: string }) {
  const a = authors.join(", ");
  const unit = org && org !== "/" ? org : ""; // the root unit says nothing; an org path is not a sentence
  return (
    <p className="citeline trunc" title={[citeLine(authors, venue, year), unit].filter(Boolean).join(" ")}>
      {a}{a && (venue || year != null) ? ". " : ""}{venue && <i>{venue}</i>}{venue && year != null ? ", " : ""}{year ?? ""}{a || venue || year != null ? "." : ""}{unit && <span className="citeorg">{a || venue || year != null ? " " : ""}{unit}</span>}
    </p>
  );
}
