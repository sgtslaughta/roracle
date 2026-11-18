import { describe, expect, it } from "vitest";
import { docsUrl, loadTopic, screenshot, topicTitle } from "./content";
import { findTopic, TOPICS, topicForPath, visibleTopics } from "./manifest";

describe("help manifest", () => {
  it("every topic file exists in the bundled glob", async () => {
    const missing: string[] = [];
    for (const t of TOPICS) if (!/^# /m.test(await loadTopic(t.id).catch(() => ""))) missing.push(t.file);
    expect(missing).toEqual([]);
  });
  it("hides admin and pats topics", () => {
    expect(visibleTopics(false, true).some((t) => t.group === "Admin")).toBe(false);
    expect(visibleTopics(true, false).some((t) => t.pats)).toBe(false);
    expect(visibleTopics(true, true)).toHaveLength(TOPICS.length);
  });
  it("maps routes to topics by longest prefix", () => {
    expect(topicForPath("/library")).toBe("library-search");
    expect(topicForPath("/d/x/v/2")).toBe("reader");
    expect(topicForPath("/zzz")).toBe("getting-started");
  });
  it("exposes every admin guide and maps admin routes", () => {
    const ids = TOPICS.filter((t) => t.group === "Admin").map((t) => t.id);
    for (const n of ["connectors", "audit", "eval", "index-maintenance", "quotas-limits", "writing-profiles"]) expect(ids).toContain(`admin-${n}`);
    expect(topicForPath("/admin/connectors/3")).toBe("admin-connectors");
    expect(topicForPath("/admin/audit")).toBe("admin-audit");
    expect(topicForPath("/admin/users")).toBe("admin-users-groups");
    expect(topicForPath("/admin/tags")).toBe("admin-README");
  });
  it("findTopic respects visibility", () => {
    expect(findTopic("admin-README", false, true)).toBeUndefined();
    expect(findTopic("admin-README", true, true)?.id).toBe("admin-README");
  });
  it("helpers", () => {
    expect(topicTitle("# Hi there\n\nx")).toBe("Hi there");
    expect(docsUrl("user/chat.md")).toBe("/docs/user/chat/");
    expect(docsUrl("admin/README.md")).toBe("/docs/admin/");
    expect(docsUrl("glossary.md")).toBe("/docs/glossary/");
    expect(screenshot("tokens-create.png")).toBeTruthy();
  });
});
