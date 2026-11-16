import { describe, expect, it } from "vitest";
import type { Bbox } from "@/api/types";
import { anchorBbox, chunkAt, noteBbox, quoteIn } from "./anchor";

const H = 792;
const bl: Bbox = { l: 60, t: 700, r: 552, b: 640, coord_origin: "BOTTOMLEFT" };

describe("noteBbox / anchorBbox", () => {
  it("maps x0,y0,x1,y1 to l,t,r,b (the backend copies l->x0, t->y0, r->x1, b->y1)", () => {
    expect(noteBbox({ x0: 60, y0: 700, x1: 552, y1: 640, coord_origin: "BOTTOMLEFT" })).toEqual(bl);
  });
  it("round-trips", () => {
    expect(noteBbox(anchorBbox(bl))).toEqual(bl);
    expect(anchorBbox(bl)).toEqual({ x0: 60, y0: 700, x1: 552, y1: 640, coord_origin: "BOTTOMLEFT" });
  });
  it("keeps TOPLEFT and defaults a missing origin to BOTTOMLEFT (docling's default)", () => {
    expect(noteBbox({ x0: 1, y0: 2, x1: 3, y1: 4, coord_origin: "TOPLEFT" }).coord_origin).toBe("TOPLEFT");
    expect(noteBbox({ x0: 1, y0: 2, x1: 3, y1: 4, coord_origin: null }).coord_origin).toBe("BOTTOMLEFT");
  });
});

describe("chunkAt", () => {
  const chunks = [
    { chunk_id: "a", page_no: 1, bbox: bl },
    { chunk_id: "b", page_no: 2, bbox: bl },
    { chunk_id: "c", page_no: 2, bbox: { l: 60, t: 300, r: 552, b: 400, coord_origin: "TOPLEFT" as const } },
  ];
  it("finds the chunk under a top-left point in points on that page (BOTTOMLEFT flipped)", () => {
    // BOTTOMLEFT t=700,b=640 -> top-left y 92..152
    expect(chunkAt(chunks, 2, { x: 100, y: 120 }, H)?.chunk_id).toBe("b");
    expect(chunkAt(chunks, 1, { x: 100, y: 120 }, H)?.chunk_id).toBe("a");
  });
  it("handles TOPLEFT boxes", () => {
    expect(chunkAt(chunks, 2, { x: 100, y: 350 }, H)?.chunk_id).toBe("c");
  });
  it("returns null off any chunk", () => {
    expect(chunkAt(chunks, 2, { x: 10, y: 120 }, H)).toBeNull();
    expect(chunkAt(chunks, 3, { x: 100, y: 120 }, H)).toBeNull();
  });
});

describe("quoteIn", () => {
  const items = [
    { str: "Title line", transform: [17, 0, 0, 17, 64, 720] },
    { str: "We conjecture an", transform: [11, 0, 0, 11, 64, 690] },
    { str: "analogous   bound.", transform: [11, 0, 0, 11, 64, 675] },
    { str: "Elsewhere", transform: [11, 0, 0, 11, 64, 600] },
  ];
  it("joins the text whose baseline origin lies inside the box", () => {
    expect(quoteIn(items, { l: 60, t: 702, r: 552, b: 660, coord_origin: "BOTTOMLEFT" }, H)).toBe("We conjecture an analogous bound.");
  });
  it("works for TOPLEFT boxes", () => {
    expect(quoteIn(items, { l: 60, t: 90, r: 552, b: 132, coord_origin: "TOPLEFT" }, H)).toBe("We conjecture an analogous bound.");
  });
  it("caps at 1000 characters", () => {
    const long = [{ str: "x".repeat(1500), transform: [1, 0, 0, 1, 70, 680] }];
    expect(quoteIn(long, bl, H)).toHaveLength(1000);
  });
});
