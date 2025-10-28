import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchStatus,
  postCourse,
  postMissionDecision,
  postRadarLock,
  postRadarUnlock,
  postWeaponArm,
  postWeaponFire,
  postWeaponSafe,
  postSpeed,
} from "./client";

vi.mock("../config", () => ({ getApiKey: () => "token" }));

const sample = {
  ship: { cell: "K13", x_nm: 10, y_nm: 10, heading_deg: 90, speed_kts: 12, hud: "Ship" },
  radar: { locked_contact_id: null, contacts: [{ id: 1, label: "Bogey", allegiance: "Hostile", range_nm: 10, bearing_deg: 90, heading_deg: 180, speed_kts: 400, category: "Aircraft", primary_weapon: null, cell: "AA00" }] },
  weather: { wind_dir_deg: 120, wind_speed_kts: 14, sea_state: 3 },
  radio: { messages: [] },
  nav_history: { entries: [] },
  cap_history: { entries: [] },
  mission: { status: "in_progress", decision: null },
  cap: { status: "ready", sorties: 0, time_in_status_s: 0, harriers: [] },
  wave: {
    label: "Calm Seas",
    elapsed_s: 10,
    duration_s: 480,
    remaining_s: 470,
    spawn_rate_per_min: 0,
    friendly_prob: 1,
    direction_bearing: 315,
  },
  health: { assets: [] },
  audio: { events: [] },
  weapons: {
    slots: [
      {
        name: "Sea Dart Fwd",
        state: "Armed",
        ammo: 12,
        max_ammo: 13,
        min_range_nm: 2.0,
        max_range_nm: 35.0,
        supports: ["Aircraft", "Ship"],
        ammo_per_shot: 1,
        category: "SAM",
        cooldown_remaining_s: 0,
      },
    ],
  },
};

describe("api client", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("parses json payload", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sample) });
    const status = await fetchStatus();
    expect(status.ship.cell).toBe("K13");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/status",
      expect.objectContaining({ headers: expect.objectContaining({ "X-Falkland-Key": "token" }) })
    );
  });

  it("throws on HTTP failure", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    await expect(fetchStatus()).rejects.toThrow();
  });

  it("posts course with headers", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sample) });
    await postCourse(220);
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/nav/course",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Falkland-Key": "token" }),
        method: "POST",
      })
    );
  });

  it("posts speed", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sample) });
    await postSpeed(18);
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/nav/speed",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Falkland-Key": "token" }),
        method: "POST",
      })
    );
  });

  it("posts mission decision", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sample) });
    await postMissionDecision("abandon_ship", "accept");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/mission/decision",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Falkland-Key": "token" }),
        method: "POST",
      })
    );
  });

  it("posts weapon arm", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sample) });
    await postWeaponArm("Sea Dart Fwd");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/weapons/arm",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Falkland-Key": "token" }),
        method: "POST",
      })
    );
  });

  it("posts weapon safe", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sample) });
    await postWeaponSafe("Sea Dart Fwd");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/weapons/safe",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Falkland-Key": "token" }),
        method: "POST",
      })
    );
  });

  it("posts weapon fire", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sample) });
    await postWeaponFire("Sea Dart Fwd");
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/weapons/fire",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Falkland-Key": "token" }),
        method: "POST",
      })
    );
  });

  it("posts radar lock", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sample) });
    await postRadarLock(1);
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/radar/lock",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Falkland-Key": "token" }),
        method: "POST",
      })
    );
  });

  it("posts radar unlock", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sample) });
    await postRadarUnlock();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/radar/unlock",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Falkland-Key": "token" }),
        method: "POST",
      })
    );
  });
});
