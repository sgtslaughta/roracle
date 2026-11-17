import { describe, expect, it } from "vitest";
import { initials } from "./initials";

describe("initials", () => {
  it.each([
    ["ada.lovelace", "AL"], ["Grace Hopper", "GH"], ["research", "RE"], ["x", "X"], ["", "?"], [undefined, "?"],
  ])("%s -> %s", (n, out) => expect(initials(n)).toBe(out));
});
