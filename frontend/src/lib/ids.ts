/** Boundary validation for ids that come from the URL. Anything else is "not found" and sends no request. */
export const isDocId = (s: unknown): s is string =>
  typeof s === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
export const isVersion = (s: unknown): s is string => typeof s === "string" && /^[1-9]\d{0,5}$/.test(s);
export const isChunkId = (s: unknown): s is string => typeof s === "string" && /^[A-Za-z0-9:_.-]{1,128}$/.test(s) && !/^\.+$/.test(s);
