import { describe, expect, it } from "vitest";
import { failureText } from "./status";

describe("failureText", () => {
  it("turns known ingest reasons into plain sentences", () => {
    expect(failureText("no text extracted")).toMatch(/^No text could be read from this file\./);
    expect(failureText("document could not be converted (unsupported or invalid file)")).toBe("This file couldn't be read. It may be damaged, or its type isn't supported.");
    expect(failureText("invalid JSON")).toBe("This file isn't valid JSON.");
    expect(failureText("archive could not be read: bad zip")).toMatch(/^This archive couldn't be unpacked/);
  });
  it("capitalises and ends anything else; empty is a generic sentence", () => {
    expect(failureText("malware detected: Eicar")).toBe("Malware detected: Eicar.");
    expect(failureText("Already fine.")).toBe("Already fine.");
    expect(failureText(null)).toBe("Processing failed.");
  });
});
