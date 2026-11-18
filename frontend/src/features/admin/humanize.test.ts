import { describe, expect, it } from "vitest";
import { humanize } from "./StatusPill";

describe("humanize", () => {
  it("reads job kinds and statuses as words, keeping acronyms", () => {
    expect(humanize("acl_sync")).toBe("ACL sync");
    expect(humanize("lsh_backfill")).toBe("LSH backfill");
    expect(humanize("connector_tick")).toBe("Connector tick");
    expect(humanize("ok")).toBe("OK");
    expect(humanize("queued")).toBe("Queued");
  });
});
