import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";

const url = (p: string) => `${API_BASE}${p}`;
export const DIR_USERS = [
  { id: "11111111-1111-4111-8111-111111111111", username: "ana" },
  { id: "22222222-2222-4222-8222-222222222222", username: "ben" },
  { id: "33333333-3333-4333-8333-333333333333", username: "k.ito" },
];
const GROUPS = ["/Mathematics", "/Mathematics/Spectral Theory", "/Physics"];

/** GET /directory/users|groups?q= (q is 2-100 characters, at most 10 matches). */
export const directoryHandlers = [
  http.get(url("/directory/users"), ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") ?? "").trim().toLowerCase();
    if (q.length < 2) return HttpResponse.json({ detail: [{ msg: "String should have at least 2 characters", loc: ["query", "q"] }] }, { status: 422 });
    return HttpResponse.json({ items: DIR_USERS.filter((u) => u.username.includes(q)) });
  }),
  http.get(url("/directory/groups"), ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") ?? "").trim().toLowerCase();
    if (q.length < 2) return HttpResponse.json({ detail: [{ msg: "String should have at least 2 characters", loc: ["query", "q"] }] }, { status: 422 });
    return HttpResponse.json({ items: GROUPS.filter((g) => g.toLowerCase().includes(q)) });
  }),
];
