import { citeNumbers } from "./citeMarkers";

describe("citeNumbers", () => {
  it("reads [n], [n,m] and the [Passage n] / [Sources n, m] wording models sometimes use", () => {
    expect(citeNumbers("A [Passage 2]. B [passages 1, 3]. C [Source 2][4]. D [9].")).toEqual([2, 1, 3, 4, 9]);
  });
  it("ignores markers inside code", () => {
    expect(citeNumbers("`[Passage 5]` and [1]")).toEqual([1]);
  });
});
