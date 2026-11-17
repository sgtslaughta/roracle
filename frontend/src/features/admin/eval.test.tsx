import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { beforeEach, describe, expect, it } from "vitest";
import { createAppRouter } from "@/app/router";
import { evalOf, LONG, R5 } from "@/mocks/eval";
import { state } from "@/mocks/state";

async function renderAt(path: string, ready: string | RegExp) {
  const router = createAppRouter([path]);
  await router.load();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  await screen.findByRole("navigation", { name: "Main" });
  await screen.findByText(ready);
}
beforeEach(() => { state.admin = true; state.evalData = null; });

describe("eval trend", () => {
  it("draws one series per selected metric with a point per run, and the selector changes the series", async () => {
    const user = userEvent.setup();
    await renderAt("/admin/eval", "Runs");
    await waitFor(() => expect(screen.getAllByTestId("pt-p_ndcg@10")).toHaveLength(3));
    expect(screen.getByTestId("series-p_recall@10")).toBeInTheDocument();
    expect(screen.queryByTestId("series-p_mrr")).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "p_mrr" }));
    expect(await screen.findAllByTestId("pt-p_mrr")).toHaveLength(3);
    await user.click(screen.getByRole("checkbox", { name: "p_ndcg@10" }));
    expect(screen.queryByTestId("series-p_ndcg@10")).toBeNull();
  });

  it("surfaces the R5 refusal when a run is started, and queues a valid one", async () => {
    const user = userEvent.setup();
    await renderAt("/admin/eval", "Runs");
    await screen.findByRole("option", { name: "admins" });
    await user.selectOptions(screen.getByLabelText("Set"), "admins");
    await user.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(R5);
    await user.selectOptions(screen.getByLabelText("Set"), "scifact");
    await user.click(screen.getByRole("button", { name: "Run now" }));
    await waitFor(() => expect(evalOf().started).toEqual([{ set_id: "s1", path: "chat", judge: false }]));
  });

  it("shows worst pairs for a run, with long query text truncated and a tooltip", async () => {
    const user = userEvent.setup();
    await renderAt("/admin/eval", "Runs");
    await user.click((await screen.findAllByRole("button", { name: "Details" }))[0]);
    const cell = await screen.findByTitle(LONG);
    expect(cell).toHaveClass("trunc");
    expect(within(screen.getByRole("table", { name: "Worst pairs" })).getByText("Spectral gaps · Cheeger")).toBeInTheDocument();
  });
});

describe("eval set", () => {
  it("approves a draft, truncating long text and collapsing principals into +K", async () => {
    const user = userEvent.setup();
    await renderAt("/admin/eval/sets/s1", /Generated questions are drafts/);
    expect(await screen.findByTitle(LONG)).toHaveClass("trunc");
    expect(screen.getByRole("button", { name: "More principals for q2: 3 more" })).toBeInTheDocument();
    const draft = screen.getByText("drafted question").closest("tr")!;
    expect(within(draft).getByText("Draft")).toBeInTheDocument();
    await user.click(within(draft).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(within(screen.getByText("drafted question").closest("tr")!).queryByText("Draft")).toBeNull());
    expect(evalOf().queries.find((x) => x.id === "q3")!.status).toBe("active");
  });
});
