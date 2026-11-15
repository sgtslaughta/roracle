import { LibraryRoute } from "./LibraryRoute";

/** Deep link /d/:docId[/v/:version]: library behind, document in the reader (wired in the reader task). */
export function DocRoute() {
  return <LibraryRoute />;
}
