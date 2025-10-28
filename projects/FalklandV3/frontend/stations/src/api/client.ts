import type { StatusSnapshot } from "./types";
import { getApiKey } from "../config";

function buildHeaders(): HeadersInit {
  const headers: HeadersInit = { Accept: "application/json" };
  const apiKey = getApiKey();
  if (apiKey) {
    headers["X-Falkland-Key"] = apiKey;
  }
  return headers;
}

export async function fetchStatus(): Promise<StatusSnapshot> {
  const response = await fetch("/api/status", { headers: buildHeaders() });
  if (!response.ok) {
    throw new Error(`Failed to fetch status (${response.status})`);
  }
  return (await response.json()) as StatusSnapshot;
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      ...buildHeaders(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export function postCourse(heading_deg: number): Promise<StatusSnapshot> {
  return post<StatusSnapshot>("/api/nav/course", { heading_deg });
}

export function postSpeed(speed_kts: number): Promise<StatusSnapshot> {
  return post<StatusSnapshot>("/api/nav/speed", { speed_kts });
}

export function postMissionDecision(decision_id: string, choice: string): Promise<StatusSnapshot> {
  return post<StatusSnapshot>("/api/mission/decision", { decision_id, choice });
}

export function postWeaponArm(name: string): Promise<StatusSnapshot> {
  return post<StatusSnapshot>("/api/weapons/arm", { name });
}

export function postWeaponSafe(name: string): Promise<StatusSnapshot> {
  return post<StatusSnapshot>("/api/weapons/safe", { name });
}

export function postWeaponFire(name: string, mode: "real" | "test" = "real"): Promise<StatusSnapshot> {
  return post<StatusSnapshot>("/api/weapons/fire", { name, mode });
}

export function postRadarLock(contact_id: number): Promise<StatusSnapshot> {
  return post<StatusSnapshot>("/api/radar/lock", { contact_id });
}

export function postRadarUnlock(): Promise<StatusSnapshot> {
  return post<StatusSnapshot>("/api/radar/unlock", {});
}
