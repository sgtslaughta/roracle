import { Columns3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { COLUMNS, type Column } from "@/lib/searchParams";

export const COLUMN_LABEL: Record<Column, string> = { title: "Title", authors: "Authors", year: "Year", venue: "Venue", cited: "Cited by", votes: "Votes", added: "Added" };

export function ColumnPicker({ columns, onChange }: { columns: readonly Column[]; onChange: (cols: Column[]) => void }) {
  const toggle = (c: Column) => onChange(COLUMNS.filter((x) => (x === c ? !columns.includes(x) : columns.includes(x))));
  return (
    <Popover>
      <PopoverTrigger asChild><Button><Columns3 aria-hidden />Columns</Button></PopoverTrigger>
      <PopoverContent aria-label="Columns">
        {COLUMNS.map((c) => (
          <label key={c} className="colopt">
            <Checkbox checked={c === "title" || columns.includes(c)} disabled={c === "title"} onCheckedChange={() => toggle(c)} aria-label={COLUMN_LABEL[c]} />
            {COLUMN_LABEL[c]}
          </label>
        ))}
      </PopoverContent>
    </Popover>
  );
}
