import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Identifiers } from "./Identifiers";

describe("Identifiers", () => {
  it("renders present ids as plain text (no links) and copies one", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    render(<Identifiers ids={{ doi: "10.1/x", arxiv: "1706.03762", issn: "0378-5955" }} />);
    expect(screen.getByText("1706.03762")).toBeInTheDocument();
    expect(screen.getByText("ISSN")).toBeInTheDocument();
    expect(screen.queryByText("PMID")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Copy arXiv" }));
    expect(write).toHaveBeenCalledWith("1706.03762");
  });
  it("renders nothing without ids", () => {
    const { container } = render(<Identifiers ids={{}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
