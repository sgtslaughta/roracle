import { http, HttpResponse } from "msw";
import { API_BASE } from "@/api/client";
import { state } from "./state";

const url = (p: string) => `${API_BASE}${p}`;

export const freshOnboarding = () => ({ steps: ["search"], dismissed: false, pats_enabled: true });

export const onboardingHandlers = [
  http.get(url("/me/onboarding"), () => HttpResponse.json(state.onboarding)),
  http.put(url("/me/onboarding"), async ({ request }) => {
    const { dismissed } = (await request.json()) as { dismissed?: boolean };
    if (typeof dismissed === "boolean") state.onboarding.dismissed = dismissed;
    return HttpResponse.json(state.onboarding);
  }),
];
