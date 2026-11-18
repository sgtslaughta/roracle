import { expect, it } from "vitest";
import { resultsHeading } from "./heading";

it.each([["multi-agent", "Results for “multi-agent”"], ['"multi-agent"', "Results for “multi-agent”"],
         ['a "b" c', 'Results for a "b" c'], ["", "Library"]])("heading %s", (q, want) => expect(resultsHeading(q)).toBe(want));
