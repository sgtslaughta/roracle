import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CiteLine } from "./CiteLine";

describe("CiteLine", () => {
  it("shows an org unit without a trailing full stop, and hides the root unit", () => {
    const { container, rerender } = render(<CiteLine authors={["A. Ng"]} year={2021} org="/Mathematics/Spectral Theory" />);
    expect(container.textContent).toBe("A. Ng. 2021. /Mathematics/Spectral Theory");
    rerender(<CiteLine authors={[]} year={null} org="/" />);
    expect(container.textContent).toBe("");
  });
});
