/** 1536 -> "1.5 KB"; binary units, matching the backend's MiB-based limits. */
export function humanBytes(n: number): string {
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${i ? v.toFixed(v < 10 ? 1 : 0).replace(/\.0$/, "") : v} ${u[i]}`;
}
