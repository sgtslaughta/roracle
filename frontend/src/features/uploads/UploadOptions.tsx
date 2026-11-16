import type { Visibility } from "@/api/types";
import { VIS_LABEL } from "./status";

export interface UploadOpts { visibility: Visibility; title: string }
export const DEFAULT_OPTS: UploadOpts = { visibility: "everyone", title: "" };
const ORDER: Visibility[] = ["everyone", "org_unit", "private", "shared"];

/** Who can see new uploads, plus an optional title (used when one file is chosen). */
export function UploadOptions({ value, onChange }: { value: UploadOpts; onChange: (v: UploadOpts) => void }) {
  return (
    <div className="uopts">
      <label>Visible to
        <select className="inp" value={value.visibility} onChange={(e) => onChange({ ...value, visibility: e.target.value as Visibility })}>
          {ORDER.map((v) => <option key={v} value={v}>{VIS_LABEL[v]}</option>)}
        </select>
      </label>
      <label>Title (optional, single file)
        <input className="inp" maxLength={500} value={value.title} onChange={(e) => onChange({ ...value, title: e.target.value })} />
      </label>
      {value.visibility === "shared" && <p className="sub">Choose who to share with after the upload finishes, from the document's sharing settings. Until then only you can see it.</p>}
    </div>
  );
}
