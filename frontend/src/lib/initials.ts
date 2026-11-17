/** Avatar initials: two letters for "first last" / "first.last" names, else the first two letters. */
export const initials = (name: string | undefined) => {
  const w = (name ?? "").trim().split(/[\s._-]+/).filter(Boolean);
  return ((w.length > 1 ? w[0][0] + w[1][0] : (w[0] ?? "?").slice(0, 2)) || "?").toUpperCase();
};
