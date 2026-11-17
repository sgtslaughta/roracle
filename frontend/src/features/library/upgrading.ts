import { createContext, useContext } from "react";

/** True while the server answers with the legacy search (index upgrade not finished): new-only controls are disabled. */
export const Upgrading = createContext(false);
export const useUpgrading = () => useContext(Upgrading);
/** Sorts the legacy search understands. */
export const LEGACY_SORTS: readonly string[] = ["relevance", "newest", "votes", "title"];
/** Facets the legacy search filters on (include only). */
export const LEGACY_FACETS: readonly string[] = ["tags", "author", "file_type", "org_unit", "collection", "label"];
