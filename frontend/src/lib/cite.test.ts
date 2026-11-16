import { describe, expect, it } from "vitest";
import { chatTitle, tabLabel } from "./cite";

describe("tabLabel", () => {
  it("prefers the short cite, then a clipped title, then filename, never empty", () => {
    expect(tabLabel({ authors: ["Ito"], year: 2023 })).toBe("Ito 2023");
    expect(tabLabel({ authors: [], year: null, title: "A very long document title indeed" })).toBe("A very long document ti…");
    expect(tabLabel({ authors: [], year: null, title: "", filename: "scan.pdf" })).toBe("scan.pdf");
    expect(tabLabel({ authors: [], year: null, title: "" })).toBe("Untitled");
  });
});
describe("chatTitle", () => {
  it("blank becomes New chat", () => {
    expect(chatTitle({ title: "" })).toBe("New chat");
    expect(chatTitle({ title: "  " })).toBe("New chat");
    expect(chatTitle(undefined)).toBe("New chat");
    expect(chatTitle({ title: "Hi" })).toBe("Hi");
  });
});
