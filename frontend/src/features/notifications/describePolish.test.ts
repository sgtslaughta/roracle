import { describe, expect, it } from "vitest";
import { describe as d } from "./describe";

const id = "00000000-0000-4000-8000-000000000042";
describe("draft notifications", () => {
  it("link to the draft", () => {
    expect(d("draft.review_requested", { draft_id: id, title: "T" })).toEqual({ text: "“T” is waiting for your review.", to: `/drafts/${id}` });
    expect(d("draft.approved", { draft_id: id, title: "T", note: "great" }).text).toBe("“T” was approved and published. Note: great");
    expect(d("draft.rejected", { draft_id: id, title: "T", note: null })).toEqual({ text: "“T” was sent back.", to: `/drafts/${id}` });
    expect(d("draft.rejected", {}).to).toBe("/drafts");
  });
});
