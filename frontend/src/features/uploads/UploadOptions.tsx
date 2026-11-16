import type { Visibility } from "@/api/types";
import { useLabels } from "@/features/labels/api";
import { useMe } from "@/features/library/useMe";
import { VIS_LABEL, VIS_ORDER } from "./status";

/** `label_id` "" = the default label (sent as nothing). */
export interface UploadOpts { visibility: Visibility; title: string; label_id: string }
export const DEFAULT_OPTS: UploadOpts = { visibility: "everyone", title: "", label_id: "" };

/** Who can see new uploads, their sensitivity label (default preselected; lowering below it is admin-only) and an optional title. */
export function UploadOptions({ value, onChange }: { value: UploadOpts; onChange: (v: UploadOpts) => void }) {
  const labels = useLabels().data ?? [];
  const admin = !!useMe().data?.is_admin;
  const def = labels.find((l) => l.is_default) ?? labels[0];
  return (
    <div className="uopts">
      <label>Visible to
        <select className="inp" value={value.visibility} onChange={(e) => onChange({ ...value, visibility: e.target.value as Visibility })}>
          {VIS_ORDER.map((v) => <option key={v} value={v}>{VIS_LABEL[v]}</option>)}
        </select>
      </label>
      {def && (
        <label>Sensitivity label
          <select className="inp" value={value.label_id || def.id} onChange={(e) => onChange({ ...value, label_id: e.target.value === def.id ? "" : e.target.value })}>
            {labels.map((l) => {
              const low = !admin && l.rank < def.rank;
              return <option key={l.id} value={l.id} disabled={low}>{l.name}{l.id === def.id ? " (default)" : ""}{low ? " (only admins can go lower)" : ""}</option>;
            })}
          </select>
        </label>
      )}
      <label>Title (optional, single file)
        <input className="inp" maxLength={500} value={value.title} onChange={(e) => onChange({ ...value, title: e.target.value })} />
      </label>
      {value.visibility === "shared" && <p className="sub">Choose who to share with after the upload finishes, from the document's sharing settings. Until then only you can see it.</p>}
    </div>
  );
}
