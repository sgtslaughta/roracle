import { setupWorker } from "msw/browser";
import { handlers } from "./handlers";
import { seedDocsFixtures } from "./docsFixtures";

seedDocsFixtures();
export const worker = setupWorker(...handlers);
