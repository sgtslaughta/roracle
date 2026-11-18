// One entry per `<!-- screenshot: <id>: ... -->` placeholder in docs/user and docs/admin.
// path: route to open; admin: sign in as an admin; run(page): steps to reach the state; clip: optional crop.
const DOC1 = "/d/00000000-0000-4000-8000-000000000001";
const tab = async (p, name) => { await p.getByRole("tab", { name }).click(); await p.waitForTimeout(500); await p.mouse.move(700, 500); await p.evaluate(() => document.activeElement?.blur()); };
const dlg = (p) => p.getByRole("dialog");
const btn = (p, name) => p.getByRole("button", { name }).first();

export const SHOTS = [
  { id: "library-results", path: "/library" },
  { id: "getting-started-signed-in", path: "/library" },
  { id: "library-filters", path: "/library", run: async (p) => {
    await p.getByText("Thesis reading list", { exact: true }).last().click();
    await p.getByText("spectral-graph-theory", { exact: true }).first().click();
    await p.getByRole("checkbox", { name: "Year: 2024" }).click();
    await p.waitForTimeout(500);
  } },

  { id: "getting-started-layout", path: DOC1 },
  { id: "reader-split", path: DOC1 },
  { id: "reader-focus", path: DOC1, run: async (p) => { await btn(p, /Focus mode/).click(); await p.waitForTimeout(600); } },
  { id: "reader-identifiers", path: DOC1, clip: { x: 820, y: 56, width: 620, height: 250 } },
  { id: "reader-references", path: DOC1, run: (p) => tab(p, /References/) },
  { id: "reader-notes", path: DOC1, run: (p) => tab(p, /Notes/) },
  { id: "command-menu", path: "/library", run: async (p) => { await btn(p, "Command menu").click(); await p.keyboard.type("spectral"); await p.waitForTimeout(500); } },

  { id: "ask-answer", path: "/ask/c1", run: async (p) => { await p.mouse.move(600, 400); await p.mouse.wheel(0, -3000); await p.waitForTimeout(500); } },
  { id: "ask-passages-read", path: "/ask/c1", run: async (p) => { await btn(p, /passages read/).click(); await p.waitForTimeout(600); } },
  { id: "ask-share-dialog", path: "/ask/c1", run: async (p) => {
    await btn(p, "Share").click(); await p.waitForTimeout(600);
    const d = p.getByRole("dialog");
    await d.getByPlaceholder("/Mathematics/Spectral Theory").fill("/Mathematics/Spectral Theory"); await d.getByRole("button", { name: "Add" }).first().click();
    await d.locator("input").nth(1).fill("u-owner"); await d.getByRole("button", { name: "Add" }).nth(1).click();
    await p.waitForTimeout(400);
  } },
  { id: "ask-context-chips", path: "/library", run: async (p) => {
    // two papers open in the reader become reading chips; ignore one, then quote a passage from an earlier answer
    await p.getByRole("heading", { name: /Lower bounds/ }).click(); await p.waitForTimeout(400);
    await p.getByRole("heading", { name: /Cheeger-type/ }).click(); await p.waitForTimeout(400);
    await p.getByRole("link", { name: /Spectral gap in hy/ }).click(); await p.waitForTimeout(800);
    await btn(p, /passages read/).click(); await p.waitForTimeout(400);
    await p.getByRole("button", { name: "Quote" }).first().click(); await p.waitForTimeout(400);
    await p.getByRole("button", { name: "Ignore this document" }).first().click(); await p.waitForTimeout(400);
    await btn(p, /Hide the .* passages read/).click(); await p.mouse.move(700, 300);
  } },

  { id: "uploads-list", path: "/uploads" },
  { id: "uploads-near-dup", path: "/uploads", run: (p) => btn(p, /Needs you/).click(), el: (p) => p.locator(".trow").filter({ hasText: "Is it the same document" }) },
  { id: "uploads-needs-title", path: "/uploads", run: (p) => btn(p, /Needs you/).click(), el: (p) => p.locator(".trow").filter({ hasText: "untitled-scan" }) },

  { id: "collections-add", path: DOC1, run: (p) => btn(p, "Add to collection").click(), el: dlg },
  { id: "sharing-dialog", path: DOC1, run: async (p) => {
    await btn(p, "Sharing").click(); await p.getByRole("dialog").locator("select").selectOption("shared");
    const d = p.getByRole("dialog");
    for (const [label, q, pick] of [["Share with users", "an", "ana"], ["Share with users", "be", "ben"], ["Share with groups", "Spectral", "/Mathematics/Spectral Theory"]]) {
      await d.getByLabel(label).fill(q); await d.getByRole("button", { name: pick, exact: true }).click(); await p.waitForTimeout(300);
    }
  }, el: dlg },
  { id: "handoff-dialog", path: DOC1, run: (p) => p.getByRole("button", { name: "Continue in agent" }).last().click(), el: dlg },
  { id: "handoff-landing", path: "/h/K7MX-4PQ2" },
  { id: "settings-handoffs", path: "/settings", run: (p) => tab(p, /Handoffs/) },
  { id: "tokens-create", path: "/settings", run: (p) => btn(p, "New token").click(), el: dlg },
  { id: "tokens-list", path: "/settings" },
  { id: "agent-proposals", path: "/proposals", full: true },

  { id: "notifications-bell", path: "/library", run: (p) => btn(p, /^Notifications/).click() },
  { id: "drafts-list", path: "/drafts" },

  { id: "drafts-checks", path: "/drafts", run: async (p) => {
    await p.getByText("Spectral gaps of random hypergraphs").first().click(); await p.waitForTimeout(600);
    await btn(p, "Run validation").click(); await p.waitForTimeout(500);
    await btn(p, "Check citations").click(); await p.waitForTimeout(700);
    await p.mouse.move(800, 500); await p.mouse.wheel(0, 500); await p.waitForTimeout(400);
  } },
  { id: "drafts-submit-dialog", path: "/drafts", run: async (p) => {
    await p.getByText("Spectral gaps of random hypergraphs").first().click(); await p.waitForTimeout(600);
    await btn(p, /^Submit/).click(); await p.waitForTimeout(500);
  }, el: dlg },

  { id: "labels-badges", path: DOC1 },
  { id: "admin-nav", path: "/admin", admin: true },
  { id: "admin-connectors", path: "/admin/connectors", admin: true },
  { id: "admin-labels", path: "/admin/labels", admin: true },
  { id: "admin-eval", path: "/admin/eval", admin: true },
  { id: "admin-users", path: "/admin/users", admin: true },
  { id: "admin-settings-limits", path: "/admin/settings", admin: true, run: async (p) => {
    await p.mouse.move(800, 500); await p.mouse.wheel(0, 380); await p.waitForTimeout(400);
  } },
  { id: "admin-settings-table", path: "/admin/settings", admin: true, run: async (p) => {
    // one override row next to the env rows
    const row = p.getByRole("row").filter({ hasText: "rerank_depth" });
    await row.locator("input").fill("60"); await btn(p, "Save rerank_depth").click(); await p.waitForTimeout(5000); // let the toast go
  } },
  { id: "admin-audit", path: "/admin/audit", admin: true, run: async (p) => {
    await p.getByPlaceholder("admin.").fill("admin.settings"); await p.getByPlaceholder("user uuid").fill("9f3c2d1e-5b7a-4c68-8e21-0a4d6b7c8e90");
    await p.waitForTimeout(800);
    await p.getByRole("button", { name: /^More/ }).first().click(); await p.waitForTimeout(400);
  } },

  { id: "admin-connector-form", path: "/admin/connectors", admin: true, run: async (p) => {
    await btn(p, "New connector").click(); const d = p.getByRole("dialog");
    await d.locator("input").first().fill("Group share bucket");
    await d.getByPlaceholder("https://s3.example.org").fill("https://s3.example.internal");
    await d.locator("input").nth(2).fill("group-papers");
  }, el: dlg }, // the Cancel/Create footer renders outside the dialog box in mock mode, so it is not in the crop
  { id: "admin-connector-runs", path: "/admin/connectors", admin: true, run: async (p) => {
    await p.getByText("Group share", { exact: true }).first().click(); await p.waitForTimeout(600);
    await p.getByRole("button", { name: "View log" }).nth(1).click(); await p.waitForTimeout(600);
    await p.mouse.move(800, 600); await p.mouse.wheel(0, 500); await p.waitForTimeout(400);
  } },
  { id: "admin-label-form", path: "/admin/labels", admin: true, run: (p) => btn(p, "New label").click(), el: dlg },
  { id: "admin-label-lower", path: DOC1, admin: true, run: async (p) => {
    await btn(p, /^Change label of/).click(); await dlg(p).locator("select").selectOption({ label: "General" }); await p.waitForTimeout(400);
  }, el: dlg },
  { id: "labels-change-dialog", path: DOC1, run: (p) => btn(p, /^Change label of/).click(), el: dlg },
];
