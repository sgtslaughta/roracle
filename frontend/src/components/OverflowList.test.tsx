import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { OverflowList } from "./OverflowList";

const make = (n: number) => Array.from({ length: n }, (_, i) => `tag-${i + 1}`);
const ui = (items: string[], max: number, extra = {}) => (
  <OverflowList items={items} max={max} menuLabel="More tags" getLabel={(s) => s} renderItem={(s) => <span key={s} data-testid="item">{s}</span>} {...extra} />
);

describe("OverflowList", () => {
  it("renders max items plus a +K chip", () => {
    render(ui(make(7), 4));
    expect(screen.getAllByTestId("item").map((e) => e.textContent)).toEqual(["tag-1", "tag-2", "tag-3", "tag-4"]);
    expect(screen.getByRole("button", { name: "More tags: 3 more" })).toHaveTextContent("+3");
  });

  it("shows no chip when everything fits", () => {
    render(ui(make(4), 4));
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("the menu lists the rest, without a filter for 10 or fewer", async () => {
    render(ui(make(7), 4));
    await userEvent.click(screen.getByRole("button", { name: /More tags|Show all/ }));
    const menu = await screen.findByLabelText("More tags", { selector: ".ovl-menu" });
    expect(within(menu).getAllByTestId("item").map((e) => e.textContent)).toEqual(["tag-5", "tag-6", "tag-7"]);
    expect(within(menu).queryByRole("searchbox")).toBeNull();
  });

  it("a filter input appears when the menu has more than 10 items and narrows the list", async () => {
    render(ui(make(20), 4));
    await userEvent.click(screen.getByRole("button", { name: /More tags|Show all/ }));
    const menu = await screen.findByLabelText("More tags", { selector: ".ovl-menu" });
    expect(within(menu).getAllByTestId("item")).toHaveLength(16);
    await userEvent.type(within(menu).getByRole("searchbox"), "tag-17");
    expect(within(menu).getAllByTestId("item").map((e) => e.textContent)).toEqual(["tag-17"]);
  });

  it("stack layout with searchable lists all values in the menu and a custom label", async () => {
    render(ui(make(12), 8, { layout: "stack", searchable: true, moreLabel: (k: number) => `Show all ${k + 8}` }));
    await userEvent.click(screen.getByRole("button", { name: /More tags|Show all/ }));
    const menu = await screen.findByLabelText("More tags", { selector: ".ovl-menu" });
    expect(within(menu).getAllByTestId("item")).toHaveLength(12);
    expect(within(menu).getByRole("searchbox")).toBeInTheDocument();
  });

  it("renders a footer in the menu", async () => {
    render(ui(make(7), 4, { footer: <button>Clear all</button> }));
    await userEvent.click(screen.getByRole("button", { name: /More tags|Show all/ }));
    expect(await screen.findByRole("button", { name: "Clear all" })).toBeInTheDocument();
  });
});
