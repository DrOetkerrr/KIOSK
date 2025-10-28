import { ChangeEvent, KeyboardEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchStatus,
  postCourse,
  postMissionDecision,
  postRadarLock,
  postRadarUnlock,
  postSpeed,
  postWeaponArm,
  postWeaponFire,
  postWeaponSafe,
} from "./api/client";
import type {
  MissionDecisionSnapshot,
  NavCommandEntry,
  RadarContact,
  RadioMessage,
  SeaHarrierStatusSnapshot,
  ShotInFlight,
  StatusSnapshot,
  WeaponSlotSnapshot,
} from "./api/types";

type StationKey = "NAV" | "RADAR" | "WPN";

const STATION_KEYS: StationKey[] = ["NAV", "RADAR", "WPN"];
const STATION_LABELS: Record<StationKey, string> = {
  NAV: "NAV",
  RADAR: "RADAR",
  WPN: "WPN",
};

type NavPanelKey = "overview" | "fleet" | "history";

const NAV_PANEL_KEYS: NavPanelKey[] = ["overview", "fleet", "history"];
const NAV_PANEL_LABELS: Record<NavPanelKey, string> = {
  overview: "Overview",
  fleet: "Fleet",
  history: "History",
};
const NAV_PANEL_IDS: Record<NavPanelKey, string> = {
  overview: "nav-panel-overview",
  fleet: "nav-panel-fleet",
  history: "nav-panel-history",
};

type RadarPanelKey = "hostiles" | "friendlies";

const RADAR_PANEL_KEYS: RadarPanelKey[] = ["hostiles", "friendlies"];
const RADAR_PANEL_LABELS: Record<RadarPanelKey, string> = {
  hostiles: "Hostiles",
  friendlies: "Friendlies",
};
const RADAR_PANEL_IDS: Record<RadarPanelKey, string> = {
  hostiles: "radar-panel-hostiles",
  friendlies: "radar-panel-friendlies",
};

type FeedbackTone = "muted" | "pending" | "ok" | "err";

interface CommandFeedback {
  tone: FeedbackTone;
  text: string;
  detail: string | null;
}

const makeFeedback = (): CommandFeedback => ({
  tone: "muted",
  text: "",
  detail: null,
});

interface AggregatedWeaponSlot {
  name: string;
  state: string;
  ammo: number;
  max_ammo: number;
  min_range_nm: number | null;
  max_range_nm: number | null;
  supports: string[];
  category: string;
  cooldown_remaining_s: number;
  children: WeaponSlotSnapshot[];
}

function aggregateWeaponSlots(slots: WeaponSlotSnapshot[]): AggregatedWeaponSlot[] {
  const groups = new Map<string, { slot: AggregatedWeaponSlot; states: Set<string> }>();

  const normaliseName = (name: string): string => {
    const trimmed = name.replace(/\s+(Fwd|Aft)$/i, "");
    return trimmed || name;
  };

  for (const slot of slots) {
    const key = normaliseName(slot.name);
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, {
        slot: {
          name: key,
          state: slot.state,
          ammo: slot.ammo,
          max_ammo: slot.max_ammo,
          min_range_nm: slot.min_range_nm ?? null,
          max_range_nm: slot.max_range_nm ?? null,
          supports: [...slot.supports],
          category: slot.category,
          cooldown_remaining_s: slot.cooldown_remaining_s,
          children: [slot],
        },
        states: new Set([slot.state]),
      });
    } else {
      const agg = existing.slot;
      existing.states.add(slot.state);
      agg.children.push(slot);
      agg.ammo += slot.ammo;
      agg.max_ammo += slot.max_ammo;
      if (slot.min_range_nm != null) {
        agg.min_range_nm =
          agg.min_range_nm == null ? slot.min_range_nm : Math.min(agg.min_range_nm, slot.min_range_nm);
      }
      if (slot.max_range_nm != null) {
        agg.max_range_nm =
          agg.max_range_nm == null ? slot.max_range_nm : Math.max(agg.max_range_nm, slot.max_range_nm);
      }
      agg.supports = Array.from(new Set([...agg.supports, ...slot.supports]));
      if (slot.category) {
        agg.category = slot.category;
      }
      if (slot.cooldown_remaining_s < agg.cooldown_remaining_s) {
        agg.cooldown_remaining_s = slot.cooldown_remaining_s;
      }
    }
  }

  return Array.from(groups.values()).map(({ slot, states }) => {
    if (states.size === 1) {
      slot.state = Array.from(states)[0];
    } else if (states.has("Armed") && states.has("Safe")) {
      slot.state = "Partial";
    } else {
      slot.state = Array.from(states)[0];
    }
    return slot;
  });
}

const WEAPON_PRIORITY: string[] = [
  "Sea Dart",
  "MM38 Exocet",
  "4.5 inch Mk.8 gun",
  "20mm GAM-BO1 (twin)",
  "20mm Oerlikon",
  "Corvus chaff",
];

const WEAPON_DISPLAY: Record<string, string> = {
  "Sea Dart": "SEA DART",
  "MM38 Exocet": "EXOCET",
  "4.5 inch Mk.8 gun": "MAIN GUN",
  "20mm GAM-BO1 (twin)": "20MM GUN",
  "20mm Oerlikon": "OERLIKON",
  "Corvus chaff": "CHAFF",
};

const weaponOrderIndex = (name: string): number => {
  const base = normaliseWeaponName(name);
  const index = WEAPON_PRIORITY.findIndex((entry) => entry.toLowerCase() === base.toLowerCase());
  return index === -1 ? WEAPON_PRIORITY.length + name.toLowerCase().charCodeAt(0) : index;
};

const normaliseWeaponName = (name: string): string => name.replace(/\s+(Fwd|Aft)$/i, "").trim();

const weaponDisplayName = (name: string): string => {
  const base = normaliseWeaponName(name);
  const key = WEAPON_PRIORITY.find((entry) => entry.toLowerCase() === base.toLowerCase());
  return key ? WEAPON_DISPLAY[key] : name.toUpperCase();
};

const formatShotEta = (shot: ShotInFlight): string => {
  if (shot.result) {
    return "—";
  }
  if (shot.eta_s <= 0.5) {
    return "IMPACT";
  }
  return `${Math.round(shot.eta_s)}s`;
};

const shotResultLabel = (shot: ShotInFlight): string => {
  if (shot.result) {
    return shot.result;
  }
  return shot.mode === "test" ? "TEST" : "PENDING";
};

const shotResultClass = (shot: ShotInFlight): string => {
  const result = (shot.result || "").toLowerCase();
  if (result === "hit") return "shot-result hit";
  if (result === "miss") return "shot-result miss";
  if (result === "deployed") return "shot-result deployed";
  if (result === "test") return "shot-result test";
  return "shot-result pending";
};

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) {
    return "—";
  }
  const value = Math.max(0, seconds);
  if (value < 60) {
    return `${Math.round(value)}s`;
  }
  if (value < 3600) {
    const minutes = Math.floor(value / 60);
    const remaining = Math.round(value % 60);
    return remaining ? `${minutes}m ${remaining}s` : `${minutes}m`;
  }
  const hours = Math.floor(value / 3600);
  const minutes = Math.round((value % 3600) / 60);
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

function formatTimestamp(ts: number | null | undefined): string {
  if (typeof ts !== "number" || Number.isNaN(ts)) {
    return "—";
  }
  const milliseconds = ts > 1e12 ? ts : ts * 1000;
  const date = new Date(milliseconds);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatShipSpeed(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }
  return `${value.toFixed(1)} kts`;
}

function formatDistanceNm(range: number | null | undefined, decimals = 1): string {
  if (typeof range !== "number" || Number.isNaN(range)) {
    return "—";
  }
  return `${range.toFixed(decimals)} nm`;
}

function titleCase(value: string): string {
  return value
    .replace(/_/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function missionStatusClass(status: string): string {
  const key = status.toLowerCase();
  if (["success", "complete", "completed"].includes(key)) return "complete";
  if (["failed", "failure", "abort", "aborted"].includes(key)) return "failed";
  if (["paused", "hold"].includes(key)) return "paused";
  if (["in_progress", "active"].includes(key)) return "active";
  return "idle";
}

function formatRange(min: number | null, max: number | null): string {
  if (min != null && max != null) {
    return `${min.toFixed(1)} – ${max.toFixed(1)} nm`;
  }
  if (min != null) {
    return `≥ ${min.toFixed(1)} nm`;
  }
  if (max != null) {
    return `≤ ${max.toFixed(1)} nm`;
  }
  return "—";
}

function formatNavCommandValue(entry: NavCommandEntry): string {
  const { action, value } = entry;
  if (!Number.isFinite(value)) {
    return String(value);
  }
  const lower = action.toLowerCase();
  if (lower.includes("course") || lower.includes("heading")) {
    return `${Math.round(value)}°`;
  }
  if (lower.includes("speed")) {
    return `${value.toFixed(1)} kts`;
  }
  return value.toFixed(1);
}

function extractDecisionOptions(decision: MissionDecisionSnapshot | null): string[] {
  if (!decision) {
    return [];
  }
  const raw = (decision as unknown as { options?: unknown[] }).options;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .map((entry) => {
      if (!entry) return "";
      if (typeof entry === "string") return entry.trim();
      if (typeof entry === "object") {
        const option = entry as { label?: string; text?: string; id?: string };
        return (option.label ?? option.text ?? option.id ?? "").toString().trim();
      }
      return "";
    })
    .filter(Boolean);
}

function isHostile(contact: RadarContact): boolean {
  return (contact.allegiance ?? "").toLowerCase() === "hostile";
}

function isFriendly(contact: RadarContact): boolean {
  const allegiance = (contact.allegiance ?? "").toLowerCase();
  if (allegiance === "friendly") {
    return true;
  }
  const id = contact.id != null ? String(contact.id) : "";
  return id.startsWith("fleet:");
}

function formatBearing(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }
  return `${Math.round(value)}°`;
}

function formatSpeed(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }
  return `${Math.round(value)} kts`;
}

function formatRangeForContact(contact: RadarContact): string {
  const range = Number(contact.range_nm);
  if (!Number.isFinite(range)) {
    return "—";
  }
  const decimals = isHostile(contact) ? 2 : 1;
  return `${range.toFixed(decimals)} nm`;
}

function computeTTILabel(contact: RadarContact): string {
  if (!isHostile(contact)) {
    return "—";
  }
  const range = Number(contact.range_nm);
  const speed = Number(contact.speed_kts);
  if (!Number.isFinite(range) || !Number.isFinite(speed) || speed <= 0) {
    return "—";
  }
  const seconds = Math.max(0, Math.round((range * 3600) / speed));
  if (seconds >= 90) {
    const minutes = Math.floor(seconds / 60);
    const remaining = seconds % 60;
    return remaining ? `${minutes}m ${remaining}s` : `${minutes}m`;
  }
  return `${seconds}s`;
}

function describeContactType(contact: RadarContact): string {
  if (contact.category) {
    return contact.category;
  }
  if (contact.primary_weapon) {
    return contact.primary_weapon;
  }
  return contact.allegiance;
}

interface StatusState {
  data: StatusSnapshot | null;
  loading: boolean;
  error: string | null;
}

export function App(): JSX.Element {
  const [state, setState] = useState<StatusState>({ data: null, loading: true, error: null });
  const [decisionMessage, setDecisionMessage] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [decisionSubmitting, setDecisionSubmitting] = useState(false);
  const [weaponMessage, setWeaponMessage] = useState<string | null>(null);
  const [weaponError, setWeaponError] = useState<string | null>(null);
  const [weaponPending, setWeaponPending] = useState<string | null>(null);
  const [courseInput, setCourseInput] = useState("");
  const [courseDirty, setCourseDirty] = useState(false);
  const [courseFeedback, setCourseFeedback] = useState<CommandFeedback>(() => makeFeedback());
  const [speedInput, setSpeedInput] = useState("");
  const [speedDirty, setSpeedDirty] = useState(false);
  const [speedFeedback, setSpeedFeedback] = useState<CommandFeedback>(() => makeFeedback());
  const [activeStation, setActiveStation] = useState<StationKey>("NAV");
  const [navPanel, setNavPanel] = useState<NavPanelKey>("overview");
  const [radarShowFriendlies, setRadarShowFriendlies] = useState<boolean>(() => {
    if (typeof window === "undefined") {
      return true;
    }
    try {
      return window.localStorage.getItem("radar_show_friendlies") !== "0";
    } catch {
      return true;
    }
  });
  const [radarShowHostiles, setRadarShowHostiles] = useState<boolean>(() => {
    if (typeof window === "undefined") {
      return true;
    }
    try {
      return window.localStorage.getItem("radar_show_hostiles") !== "0";
    } catch {
      return true;
    }
  });
  const [radarLockError, setRadarLockError] = useState<string | null>(null);
  const [radarLockPending, setRadarLockPending] = useState<number | "unlock" | null>(null);

  const loadStatus = useCallback(async (): Promise<boolean> => {
    try {
      const data = await fetchStatus();
      setState({ data, loading: false, error: null });
      setDecisionMessage(null);
      setDecisionError(null);
      setWeaponMessage(null);
      setWeaponError(null);
      setRadarLockError(null);
      setStatusUpdatedAt(Date.now());
      return true;
    } catch (error) {
      setState({ data: null, loading: false, error: (error as Error).message });
      return false;
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      loadStatus();
    }, 2500);
    return () => window.clearInterval(interval);
  }, [loadStatus]);

  const shipHeading = state.data?.ship.heading_deg;
  const shipSpeed = state.data?.ship.speed_kts;

  useEffect(() => {
    if (!state.data) {
      return;
    }
    if (!courseDirty) {
      const heading = shipHeading != null && Number.isFinite(shipHeading) ? shipHeading.toFixed(0) : "";
      setCourseInput(heading);
    }
    if (!speedDirty) {
      const speed = shipSpeed != null && Number.isFinite(shipSpeed) ? shipSpeed.toFixed(1) : "";
      setSpeedInput(speed);
    }
  }, [state.data, shipHeading, shipSpeed, courseDirty, speedDirty]);

  const rawWeaponSlots = state.data?.weapons?.slots ?? null;
  const aggregatedWeaponSlots = useMemo(() => {
    const aggregated = aggregateWeaponSlots(rawWeaponSlots ?? []);
    aggregated.sort((a, b) => weaponOrderIndex(a.name) - weaponOrderIndex(b.name));
    return aggregated;
  }, [rawWeaponSlots]);

  const radarContactsRaw: RadarContact[] = state.data?.radar?.contacts ?? [];
  const lockedContactId = state.data?.radar?.locked_contact_id ?? null;
  const radarSummary = useMemo(() => {
    const source = Array.isArray(radarContactsRaw) ? radarContactsRaw : [];
    let friendlyCount = 0;
    let hostileCount = 0;
    const filtered: RadarContact[] = [];
    for (const contact of source) {
      const friendly = isFriendly(contact);
      const hostile = isHostile(contact);
      if (friendly) friendlyCount += 1;
      if (hostile) hostileCount += 1;
      const isLocked = lockedContactId != null && contact.id === lockedContactId;
      if (!isLocked) {
        if (friendly && !radarShowFriendlies) continue;
        if (hostile && !radarShowHostiles) continue;
      }
      filtered.push(contact);
    }
    filtered.sort((a, b) => {
      if (lockedContactId != null) {
        if (a.id === lockedContactId) return -1;
        if (b.id === lockedContactId) return 1;
      }
      const aHostile = isHostile(a);
      const bHostile = isHostile(b);
      if (aHostile !== bHostile) {
        return aHostile ? -1 : 1;
      }
      const aRange = Number.isFinite(a.range_nm) ? (a.range_nm as number) : Number.POSITIVE_INFINITY;
      const bRange = Number.isFinite(b.range_nm) ? (b.range_nm as number) : Number.POSITIVE_INFINITY;
      return aRange - bRange;
    });
    return {
      filtered,
      friendlyCount,
      hostileCount,
    };
  }, [lockedContactId, radarContactsRaw, radarShowFriendlies, radarShowHostiles]);

  const shotsInFlight: ShotInFlight[] = state.data?.audio?.shots_in_flight ?? [];

  const submitCourse = async () => {
    const trimmed = courseInput.trim();
    if (!trimmed) {
      setCourseFeedback({ tone: "err", text: "ERR", detail: "Enter a heading" });
      return;
    }
    const heading = Number(trimmed);
    if (Number.isNaN(heading)) {
      setCourseFeedback({ tone: "err", text: "ERR", detail: "Enter a valid heading" });
      return;
    }
    setCourseFeedback({ tone: "pending", text: "…", detail: null });
    try {
      await postCourse(heading);
      setCourseFeedback({
        tone: "ok",
        text: "OK",
        detail: `Course set to ${heading.toFixed(0)}°`,
      });
      setCourseDirty(false);
      await loadStatus();
    } catch (error) {
      setCourseFeedback({ tone: "err", text: "ERR", detail: (error as Error).message });
    }
  };

  const submitSpeed = async () => {
    const trimmed = speedInput.trim();
    if (!trimmed) {
      setSpeedFeedback({ tone: "err", text: "ERR", detail: "Enter a speed" });
      return;
    }
    const speed = Number(trimmed);
    if (Number.isNaN(speed)) {
      setSpeedFeedback({ tone: "err", text: "ERR", detail: "Enter a valid speed" });
      return;
    }
    setSpeedFeedback({ tone: "pending", text: "…", detail: null });
    try {
      await postSpeed(speed);
      setSpeedFeedback({
        tone: "ok",
        text: "OK",
        detail: `Speed set to ${speed.toFixed(1)} kts`,
      });
      setSpeedDirty(false);
      await loadStatus();
    } catch (error) {
      setSpeedFeedback({ tone: "err", text: "ERR", detail: (error as Error).message });
    }
  };

  const handleCourseChange = (event: ChangeEvent<HTMLInputElement>) => {
    setCourseDirty(true);
    setCourseInput(event.target.value);
  };

  const handleSpeedChange = (event: ChangeEvent<HTMLInputElement>) => {
    setSpeedDirty(true);
    setSpeedInput(event.target.value);
  };

  const handleCourseKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submitCourse();
    }
  };

  const handleSpeedKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submitSpeed();
    }
  };

  const resolveDecision = async (choice: "accept" | "decline") => {
    const decision = state.data?.mission.decision;
    if (!decision || decision.status !== "pending") {
      return;
    }
    try {
      setDecisionSubmitting(true);
      setDecisionError(null);
      setDecisionMessage(null);
      const decisionId = typeof decision.id === "string" ? decision.id : "";
      const updated = await postMissionDecision(decisionId, choice);
      setState({ data: updated, loading: false, error: null });
      setDecisionMessage(`Decision ${choice} submitted`);
    } catch (error) {
      setDecisionError((error as Error).message);
    } finally {
      setDecisionSubmitting(false);
    }
  };

  const fireAggregatedWeapon = async (slot: AggregatedWeaponSlot, mode: "real" | "test" = "real") => {
    const target = slot.children.find((child) => {
      if (child.state !== "Armed" || child.ammo <= 0) {
        return false;
      }
      if (mode === "test") {
        return true;
      }
      return child.cooldown_remaining_s <= 0.1;
    });

    if (!target) {
      setWeaponError(`No ${mode === "real" ? "ready" : "armed"} launcher available for ${slot.name}`);
      return;
    }

    setWeaponPending(slot.name);
    setWeaponMessage(null);
    setWeaponError(null);
    try {
      await postWeaponFire(target.name, mode);
      setWeaponMessage(`${slot.name} fired (${mode})`);
      await loadStatus();
    } catch (error) {
      setWeaponError((error as Error).message);
    } finally {
      setWeaponPending(null);
    }
  };

  const armAggregatedWeapon = async (slot: AggregatedWeaponSlot) => {
    const target = slot.children.find((child) => child.state !== "Armed");
    if (!target) {
      return;
    }
    setWeaponPending(slot.name);
    setWeaponError(null);
    setWeaponMessage(null);
    try {
      await postWeaponArm(target.name);
      await loadStatus();
    } catch (error) {
      setWeaponError((error as Error).message);
    } finally {
      setWeaponPending(null);
    }
  };

  const safeAggregatedWeapon = async (slot: AggregatedWeaponSlot) => {
    const target = slot.children.find((child) => child.state === "Armed");
    if (!target) {
      return;
    }
    setWeaponPending(slot.name);
    setWeaponError(null);
    setWeaponMessage(null);
    try {
      await postWeaponSafe(target.name);
      await loadStatus();
    } catch (error) {
      setWeaponError((error as Error).message);
    } finally {
      setWeaponPending(null);
    }
  };

  const lockRadarContact = async (contactId: number) => {
    setRadarLockError(null);
    setRadarLockPending(contactId);
    try {
      await postRadarLock(contactId);
      await loadStatus();
    } catch (error) {
      setRadarLockError((error as Error).message);
    } finally {
      setRadarLockPending(null);
    }
  };

  const unlockRadarContact = async () => {
    setRadarLockError(null);
    setRadarLockPending("unlock");
    try {
      await postRadarUnlock();
      await loadStatus();
    } catch (error) {
      setRadarLockError((error as Error).message);
    } finally {
      setRadarLockPending(null);
    }
  };

  if (state.loading) {
    return <main className="nav-console loading">Loading station status…</main>;
  }

  if (state.error || !state.data) {
    return <main className="nav-console error">Failed to load status: {state.error}</main>;
  }

  const { ship, mission, nav_history, cap, wave, radar, radio, health } = state.data;
  const missionStatusLabel = titleCase(mission.status);
  const missionStatus = missionStatusClass(mission.status);
  const missionSequence = mission.sequence ?? null;
  const missionStage = (() => {
    if (!missionSequence?.order || !missionSequence.order.length) {
      return null;
    }
    const idx = typeof missionSequence.index === "number" ? missionSequence.index : null;
    if (idx != null && idx >= 0 && idx < missionSequence.order.length) {
      return `Stage ${idx + 1} of ${missionSequence.order.length}`;
    }
    return `${missionSequence.order.length} mission sequence`;
  })();
  const missionSettings = mission.settings ?? null;
  const trainingMode = missionSettings?.stations_offline === true;
  const hostilesSuppressed = missionSettings?.hostile_spawns === false;
  const missionAlert = mission.alert?.trim() ?? "";
  const missionOutcome = mission.outcome?.trim() ?? "";
  const pendingDecision = mission.decision && mission.decision.status === "pending" ? mission.decision : null;
  const decisionOptions = extractDecisionOptions(pendingDecision);
  const navHistoryEntriesSorted = [...nav_history.entries].sort((a, b) => {
    const aTs = typeof a.ts === "number" && Number.isFinite(a.ts) ? a.ts : 0;
    const bTs = typeof b.ts === "number" && Number.isFinite(b.ts) ? b.ts : 0;
    return bTs - aTs;
  });
  const navHistoryPreview = navHistoryEntriesSorted.slice(0, 3);
  const navHistoryPanelEntries = navHistoryEntriesSorted.slice(0, 12);
  const latestRadio: RadioMessage | undefined = radio.messages.at(-1);
  const healthAssets = Array.isArray(health.assets) ? health.assets : [];
  const findHealth = (needle: string) =>
    healthAssets.find((asset) => asset.name && asset.name.toLowerCase() === needle);
  const sheffieldHealth = findHealth("sheffield");
  const hermesHealth = findHealth("hermes");
  const glamorganHealth = findHealth("glamorgan");
  const findFriendlyContact = (needles: string[]): RadarContact | undefined => {
    const lowered = needles.map((entry) => entry.toLowerCase());
    const byLabel = radarContactsRaw.find((contact) => {
      if (!isFriendly(contact)) {
        return false;
      }
      const label = (contact.label ?? "").toLowerCase();
      return lowered.some((needle) => label.includes(needle));
    });
    if (byLabel) {
      return byLabel;
    }
    return radarContactsRaw.find((contact) => {
      if (!isFriendly(contact)) {
        return false;
      }
      const category = (contact.category ?? "").toLowerCase();
      const weapon = (contact.primary_weapon ?? "").toLowerCase();
      return lowered.some((needle) => category.includes(needle) || weapon.includes(needle));
    });
  };
  const hermesContact = findFriendlyContact(["hermes", "carrier"]);
  const glamorganContact = findFriendlyContact(["glamorgan", "destroyer"]);
  const fleetUnits = [
    {
      key: "sheffield",
      label: "Sheffield",
      grid: ship.cell || "—",
      speed: formatShipSpeed(ship.speed_kts),
      heading: formatBearing(ship.heading_deg),
      separation: "—",
      integrity:
        sheffieldHealth && typeof sheffieldHealth.lives === "number" && typeof sheffieldHealth.max_lives === "number"
          ? `${sheffieldHealth.lives}/${sheffieldHealth.max_lives}`
          : "—",
    },
    {
      key: "hermes",
      label: "Hermes",
      grid: hermesContact?.cell ?? "—",
      speed: formatShipSpeed(hermesContact?.speed_kts),
      heading: formatBearing(hermesContact?.heading_deg),
      separation: formatDistanceNm(hermesContact?.range_nm),
      integrity:
        hermesHealth && typeof hermesHealth.lives === "number" && typeof hermesHealth.max_lives === "number"
          ? `${hermesHealth.lives}/${hermesHealth.max_lives}`
          : "—",
    },
    {
      key: "glamorgan",
      label: "Glamorgan",
      grid: glamorganContact?.cell ?? "—",
      speed: formatShipSpeed(glamorganContact?.speed_kts),
      heading: formatBearing(glamorganContact?.heading_deg),
      separation: formatDistanceNm(glamorganContact?.range_nm),
      integrity:
        glamorganHealth && typeof glamorganHealth.lives === "number" && typeof glamorganHealth.max_lives === "number"
          ? `${glamorganHealth.lives}/${glamorganHealth.max_lives}`
          : "—",
    },
  ];
  const navFeedbackEntry =
    [courseFeedback, speedFeedback].find((entry) => entry.tone === "err" && entry.text) ??
    [courseFeedback, speedFeedback].find((entry) => entry.tone === "pending" && entry.text) ??
    [courseFeedback, speedFeedback].find((entry) => entry.tone === "ok" && entry.text) ??
    null;
  const navHistoryTotal = nav_history.entries.length;
  const waveRemaining = formatDuration(wave.remaining_s);
  const waveHeading = formatBearing(wave.direction_bearing);
  const waveSpawnRate =
    typeof wave.spawn_rate_per_min === "number" && Number.isFinite(wave.spawn_rate_per_min)
      ? `${wave.spawn_rate_per_min.toFixed(1)}/min`
      : "—";
  const waveFriendlyChance =
    typeof wave.friendly_prob === "number" && Number.isFinite(wave.friendly_prob)
      ? `${Math.round(wave.friendly_prob * 100)}%`
      : "—";
  const { filtered: radarFilteredContacts, friendlyCount: radarFriendlyCount, hostileCount: radarHostileCount } =
    radarSummary;
  const radarTotalCount = radarContactsRaw.length;
  const primaryContact = (() => {
    if (lockedContactId != null) {
      return (
        radarContactsRaw.find((contact) => contact.id === lockedContactId) ??
        radarFilteredContacts.find((contact) => contact.id === lockedContactId) ??
        radarFilteredContacts.find((contact) => isHostile(contact)) ??
        radarFilteredContacts[0] ??
        null
      );
    }
    return radarFilteredContacts.find((contact) => isHostile(contact)) ?? radarFilteredContacts[0] ?? null;
  })();

  const stationTabs = (
    <div className="station-tabs">
      {STATION_KEYS.map((key) => (
        <button
          key={key}
          type="button"
          className={`station-tab btn${activeStation === key ? " active" : ""}`}
          onClick={() => setActiveStation(key)}
        >
          {STATION_LABELS[key]}
        </button>
      ))}
    </div>
  );


const navSubtabs = (
  <div className="nav-subtabs" role="tablist" aria-label="Navigation panels">
    {NAV_PANEL_KEYS.map((panelKey) => (
      <button
        key={panelKey}
        id={`nav-tab-${panelKey}`}
        type="button"
        role="tab"
        aria-selected={navPanel === panelKey}
        aria-controls={NAV_PANEL_IDS[panelKey]}
        className={`btn nav-subtab${navPanel === panelKey ? " active" : ""}`}
        onClick={() => setNavPanel(panelKey)}
      >
        {NAV_PANEL_LABELS[panelKey]}
      </button>
    ))}
  </div>
);

const navOverviewPanel = (
  <section
    id={NAV_PANEL_IDS.overview}
    role="tabpanel"
    aria-labelledby="nav-tab-overview"
    className="nav-panel nav-panel-overview"
    tabIndex={navPanel === "overview" ? 0 : -1}
    hidden={navPanel !== "overview"}
  >
    <header className="nav-panel-head">
      <div className="nav-panel-title-group">
        <h2 className="nav-panel-title">{mission.label || "Mission"}</h2>
        <div className="nav-panel-meta">
          <span>Elapsed {formatDuration(mission.elapsed_s)}</span>
          <span>
            {mission.time_left_s != null && mission.time_left_s >= 0
              ? `Time left ${formatDuration(mission.time_left_s)}`
              : "No mission timer"}
          </span>
          {missionStage && <span>{missionStage}</span>}
        </div>
      </div>
      <span className={`status-badge ${missionStatus}`}>{missionStatusLabel}</span>
    </header>

    <div className="nav-overview-badges">
      {fleetUnits.map((unit) => (
        <div key={unit.key} className="nav-overview-badge">
          <span className="nav-overview-name">{unit.label}</span>
          <span className="nav-overview-grid">Grid {unit.grid}</span>
          <div className="nav-overview-figures">
            <span>Speed {unit.speed}</span>
            <span>Course {unit.heading}</span>
          </div>
          {unit.integrity !== "—" && <span className="nav-overview-integrity">Hull {unit.integrity}</span>}
          {unit.separation !== "—" && <span className="nav-overview-separation">Sep {unit.separation}</span>}
        </div>
      ))}
    </div>

    {missionAlert && <p className="nav-alert warning">{missionAlert}</p>}
    {trainingMode && (
      <p className="nav-alert info">Training mode active: stations power up manually and hostile spawns are suppressed.</p>
    )}
    {!trainingMode && hostilesSuppressed && <p className="nav-alert info">Hostile spawns disabled for this mission.</p>}
    {mission.description && <p className="nav-mission-desc">{mission.description}</p>}
    {missionOutcome && (
      <p className={`nav-alert outcome ${missionOutcome.toLowerCase().includes("fail") ? "err" : "ok"}`}>
        Outcome: {missionOutcome}
      </p>
    )}

    <div className="nav-orders">
      <div className="nav-order">
        <label htmlFor="nav-course">Set course</label>
        <div className="nav-order-controls">
          <input
            id="nav-course"
            className="input"
            value={courseInput}
            onChange={handleCourseChange}
            onKeyDown={handleCourseKey}
            placeholder="000"
            autoComplete="off"
          />
          <button type="button" className="btn nav-set-btn" onClick={submitCourse}>
            Set
          </button>
          <span className={`nav-msg ${courseFeedback.tone}`} title={courseFeedback.detail ?? undefined}>
            {courseFeedback.text}
          </span>
        </div>
      </div>
      <div className="nav-order">
        <label htmlFor="nav-speed">Set speed</label>
        <div className="nav-order-controls">
          <input
            id="nav-speed"
            className="input"
            value={speedInput}
            onChange={handleSpeedChange}
            onKeyDown={handleSpeedKey}
            placeholder="00.0"
            autoComplete="off"
          />
          <button type="button" className="btn nav-set-btn" onClick={submitSpeed}>
            Set
          </button>
          <span className={`nav-msg ${speedFeedback.tone}`} title={speedFeedback.detail ?? undefined}>
            {speedFeedback.text}
          </span>
        </div>
      </div>
    </div>

    {navFeedbackEntry && navFeedbackEntry.text && (
      <div className={`nav-feedback-strip ${navFeedbackEntry.tone}`}>
        <strong>{navFeedbackEntry.text}</strong>
        {navFeedbackEntry.detail && <span>{navFeedbackEntry.detail}</span>}
      </div>
    )}

    <div className="nav-history-preview">
      <h3>Recent Orders</h3>
      {navHistoryPreview.length ? (
        <ul>
          {navHistoryPreview.map((entry) => (
            <li key={entry.id}>
              <span className="nav-history-time">{formatTimestamp(entry.ts)}</span>
              <span className="nav-history-label">{entry.action}</span>
              <strong>{formatNavCommandValue(entry)}</strong>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No navigation orders yet.</p>
      )}
    </div>

    {pendingDecision && (
      <div className="nav-decision-card">
        <p>
          {pendingDecision.prompt || "Decision required"}
          {decisionOptions.length ? ` (${decisionOptions.join(" / ")})` : ""}
        </p>
        <div className="nav-decision-actions">
          <button type="button" onClick={() => resolveDecision("accept")} disabled={decisionSubmitting}>
            Accept
          </button>
          <button type="button" onClick={() => resolveDecision("decline")} disabled={decisionSubmitting}>
            Decline
          </button>
        </div>
        {decisionMessage && <p className="feedback success">{decisionMessage}</p>}
        {decisionError && <p className="feedback error">{decisionError}</p>}
      </div>
    )}

    {latestRadio && (
      <div className="nav-radio-strip">
        <span className="nav-radio-tag">{titleCase(latestRadio.category || "Radio")}</span>
        <span className="nav-radio-text">{latestRadio.text}</span>
      </div>
    )}
  </section>
);

const navFleetPanel = (
  <section
    id={NAV_PANEL_IDS.fleet}
    role="tabpanel"
    aria-labelledby="nav-tab-fleet"
    className="nav-panel nav-panel-fleet"
    tabIndex={navPanel === "fleet" ? 0 : -1}
    hidden={navPanel !== "fleet"}
  >
    <header className="nav-panel-head">
      <div className="nav-panel-title-group">
        <h2 className="nav-panel-title">Fleet Snapshot</h2>
        <div className="nav-panel-meta">
          <span>Total orders {navHistoryTotal}</span>
          <span>Latest {navHistoryPreview[0] ? formatTimestamp(navHistoryPreview[0].ts) : "—"}</span>
        </div>
      </div>
      <div className="nav-panel-meta nav-cap-meta">
        <span>CAP {titleCase(cap.status)}</span>
        <span>Sorties {cap.sorties}</span>
        <span>Timer {formatDuration(cap.time_in_status_s)}</span>
      </div>
    </header>
    <table className="nav-fleet-table">
      <thead>
        <tr>
          <th scope="col">Unit</th>
          <th scope="col">Grid</th>
          <th scope="col">Speed</th>
          <th scope="col">Course</th>
          <th scope="col">Separation</th>
          <th scope="col">Hull</th>
        </tr>
      </thead>
      <tbody>
        {fleetUnits.map((unit) => (
          <tr key={unit.key}>
            <th scope="row">{unit.label}</th>
            <td>{unit.grid}</td>
            <td>{unit.speed}</td>
            <td>{unit.heading}</td>
            <td>{unit.separation}</td>
            <td>{unit.integrity}</td>
          </tr>
        ))}
      </tbody>
    </table>
    <div className="nav-fleet-footnote">
      <span>Wave {wave.label || "—"}</span>
      <span>Remaining {waveRemaining}</span>
      <span>Heading {waveHeading}</span>
      <span>Spawn {waveSpawnRate}</span>
      <span>Friendly {waveFriendlyChance}</span>
    </div>
  </section>
);

const navHistoryPanel = (
  <section
    id={NAV_PANEL_IDS.history}
    role="tabpanel"
    aria-labelledby="nav-tab-history"
    className="nav-panel nav-panel-history"
    tabIndex={navPanel === "history" ? 0 : -1}
    hidden={navPanel !== "history"}
  >
    <header className="nav-panel-head">
      <div className="nav-panel-title-group">
        <h2 className="nav-panel-title">Navigation History</h2>
        <div className="nav-panel-meta">
          <span>{navHistoryTotal} orders logged</span>
          <span>Grid {ship.cell}</span>
        </div>
      </div>
      <button type="button" className="btn nav-overview-link" onClick={() => setNavPanel("overview")}>
        Back to overview
      </button>
    </header>
    {navHistoryPanelEntries.length ? (
      <ul className="nav-history-list">
        {navHistoryPanelEntries.map((entry) => (
          <li key={entry.id}>
            <span className="nav-history-time">{formatTimestamp(entry.ts)}</span>
            <span className="nav-history-label">{entry.action}</span>
            <strong>{formatNavCommandValue(entry)}</strong>
          </li>
        ))}
      </ul>
    ) : (
      <p className="muted">No navigation orders recorded yet.</p>
    )}
  </section>
);

const navContent = (
  <div className="nav-station">
    {navSubtabs}
    <div className="nav-panel-container">
      {navOverviewPanel}
      {navFleetPanel}
      {navHistoryPanel}
    </div>
  </div>
);

const weaponsContent = (
  <div className="weapons-layout">
      <section className="weapons-panel weapons-primary">
        <div className="weapons-panel-head">
          <h3>Targeting</h3>
          {lockedContactId != null ? (
            <button
              type="button"
              className="btn weapons-unlock-btn"
              onClick={() => unlockRadarContact()}
              disabled={radarLockPending !== null}
            >
              {radarLockPending === "unlock" ? "Unlocking…" : "Unlock"}
            </button>
          ) : (
            primaryContact && (
              <button
                type="button"
                className="btn weapons-lock-btn"
                onClick={() => lockRadarContact(primaryContact.id)}
                disabled={radarLockPending !== null}
              >
                {radarLockPending === primaryContact.id ? "Locking…" : "Lock Target"}
              </button>
            )
          )}
        </div>
        {radarLockError && <p className="feedback error">{radarLockError}</p>}
        {primaryContact ? (
          <div className="weapons-primary-grid">
            <div className="weapons-primary-field">
              <span>ID</span>
              <strong>{primaryContact.id}</strong>
            </div>
            <div className="weapons-primary-field">
              <span>Label</span>
              <strong>{primaryContact.label}</strong>
            </div>
            <div className="weapons-primary-field">
              <span>Status</span>
              <strong
                className={`radar-tag ${
                  isHostile(primaryContact) ? "hostile" : isFriendly(primaryContact) ? "friendly" : "unknown"
                }`}
              >
                {primaryContact.allegiance}
              </strong>
            </div>
            <div className="weapons-primary-field">
              <span>Grid</span>
              <strong>{primaryContact.cell}</strong>
            </div>
            <div className="weapons-primary-field">
              <span>Range</span>
              <strong>{formatRangeForContact(primaryContact)}</strong>
            </div>
            <div className="weapons-primary-field">
              <span>Bearing</span>
              <strong>{formatBearing(primaryContact.bearing_deg)}</strong>
            </div>
            <div className="weapons-primary-field">
              <span>Speed</span>
              <strong>{formatSpeed(primaryContact.speed_kts)}</strong>
            </div>
            <div className="weapons-primary-field">
              <span>TTI</span>
              <strong>{computeTTILabel(primaryContact)}</strong>
            </div>
            <div className="weapons-primary-field">
              <span>Type</span>
              <strong>{describeContactType(primaryContact)}</strong>
            </div>
          </div>
        ) : (
          <p className="muted">No primary target. Lock a contact from RADAR.</p>
        )}
      </section>
      <section className="weapons-panel weapons-shots">
        <h3>Shots In Flight</h3>
        {shotsInFlight.length ? (
          <table className="weapons-shots-table">
            <thead>
              <tr>
                <th>Weapon</th>
                <th>Target</th>
                <th>Grid</th>
                <th className="num">ETA</th>
                <th className="num">Pk</th>
                <th>Result</th>
                <th className="num">Range</th>
              </tr>
            </thead>
            <tbody>
              {shotsInFlight.map((shot) => (
                <tr key={shot.id} className={shot.result ? "shot-resolved" : undefined}>
                  <td>{weaponDisplayName(shot.weapon)}</td>
                  <td>{shot.target || "—"}</td>
                  <td>{shot.cell}</td>
                  <td className="num">{formatShotEta(shot)}</td>
                  <td className="num">{Math.round(shot.pk_pct)}%</td>
                  <td className={shotResultClass(shot)}>{shotResultLabel(shot)}</td>
                  <td className="num">{shot.range_nm.toFixed(1)} nm</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="weapons-shots-empty muted">No active shots.</p>
        )}
      </section>

      <section className="weapons-panel weapons-inventory">
        <h3>Weapon Slots</h3>
        {weaponError && <p className="feedback error">{weaponError}</p>}
        {weaponMessage && <p className="feedback success">{weaponMessage}</p>}
        {aggregatedWeaponSlots.length ? (
          <ul className="weapons-list">
            {aggregatedWeaponSlots.map((slot) => {
              const cooldown = Math.max(0, slot.cooldown_remaining_s);
              const lowAmmo = slot.max_ammo > 0 && slot.ammo / slot.max_ammo <= 0.25;
              const fireableChild = slot.children.find(
                (child) => child.state === "Armed" && child.ammo > 0 && child.cooldown_remaining_s <= 0.1
              );
              const testableChild = slot.children.find((child) => child.state === "Armed" && child.ammo > 0);
              const fireDisabled = weaponPending !== null || !fireableChild;
              const testDisabled = weaponPending !== null || !testableChild;
              const allArmed = slot.children.every((child) => child.state === "Armed");
              return (
                <li key={slot.name} className={`weapon-slot ${lowAmmo ? "low-ammo" : ""}`}>
                  <div className="weapon-slot__head">
                    <strong>{weaponDisplayName(slot.name)}</strong>
                    <span className={`state-label state-${slot.state.toLowerCase()}`}>{slot.state}</span>
                  </div>
                  <div className="weapon-slot__meta">
                    <span>Ammo {slot.ammo}/{slot.max_ammo}</span>
                    <span>Range {formatRange(slot.min_range_nm, slot.max_range_nm)}</span>
                    <span>Targets {slot.supports.length ? slot.supports.join(", ") : "—"}</span>
                    {slot.children.length > 1 && (
                      <span>Launchers {slot.children.map((child) => child.name).join(", ")}</span>
                    )}
                    <span>Cooldown {cooldown > 0 ? `${cooldown.toFixed(1)}s` : "Ready"}</span>
                  </div>
                  <div className="weapon-slot__actions">
                    <button type="button" onClick={() => armAggregatedWeapon(slot)} disabled={weaponPending !== null || allArmed}>
                      Arm
                    </button>
                    <button
                      type="button"
                      onClick={() => safeAggregatedWeapon(slot)}
                      disabled={weaponPending !== null || slot.children.every((child) => child.state === "Safe")}
                    >
                      Safe
                    </button>
                    <button type="button" onClick={() => fireAggregatedWeapon(slot, "real")} disabled={fireDisabled}>
                      {weaponPending === slot.name ? "Firing…" : "Fire"}
                    </button>
                    <button type="button" onClick={() => fireAggregatedWeapon(slot, "test")} disabled={testDisabled}>
                      Test
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted">No weapon slots reported.</p>
        )}
      </section>
    </div>
  );

  const radarContent = (
    <div className="radar-layout">
      <section className="radar-panel radar-overview">
        <div className="radar-toggles">
          <span>Friendlies</span>
          <button
            type="button"
            className={`btn radar-toggle ${radarShowFriendlies ? "on" : "off"}`}
            onClick={() => setRadarShowFriendlies((value) => !value)}
          >
            {radarShowFriendlies ? "ON" : "OFF"}
          </button>
          <span>Hostiles</span>
          <button
            type="button"
            className={`btn radar-toggle ${radarShowHostiles ? "on" : "off"}`}
            onClick={() => setRadarShowHostiles((value) => !value)}
          >
            {radarShowHostiles ? "ON" : "OFF"}
          </button>
        </div>
        {radarLockError && <p className="feedback error radar-lock-error">{radarLockError}</p>}
        <div className="radar-counters">
          <div className="radar-counter">
            <span>Hostiles</span>
            <strong>{radarHostileCount}</strong>
          </div>
          <div className="radar-counter">
            <span>Friendlies</span>
            <strong>{radarFriendlyCount}</strong>
          </div>
          <div className="radar-counter">
            <span>Tracks</span>
            <strong>{radarFilteredContacts.length}</strong>
            <small>of {radarTotalCount}</small>
          </div>
        </div>
        <div className="radar-wave">
          <span>Wave {wave.label || "—"}</span>
          <span>Elapsed {formatDuration(wave.elapsed_s)}</span>
          <span>Remaining {waveRemaining}</span>
          <span>Heading {waveHeading}</span>
          <span>Spawn {waveSpawnRate}</span>
          <span>Friendly {waveFriendlyChance}</span>
        </div>
      </section>

      <section className="radar-panel radar-primary">
        <div className="radar-panel-head">
          <h3>Primary Track</h3>
          {lockedContactId != null && (
            <button
              type="button"
              className="btn radar-unlock-btn"
              onClick={() => unlockRadarContact()}
              disabled={radarLockPending !== null}
            >
              {radarLockPending === "unlock" ? "Unlocking…" : "Unlock"}
            </button>
          )}
        </div>
        {primaryContact ? (
          <div className="radar-primary-grid">
            <div className="radar-primary-field">
              <span>Track</span>
              <strong>{primaryContact.label}</strong>
              {lockedContactId === primaryContact.id && <span className="radar-lock-indicator">LOCKED</span>}
            </div>
            <div className="radar-primary-field">
              <span>Grid</span>
              <strong>{primaryContact.cell}</strong>
            </div>
            <div className="radar-primary-field">
              <span>Status</span>
              <strong
                className={`radar-tag ${
                  isHostile(primaryContact) ? "hostile" : isFriendly(primaryContact) ? "friendly" : "unknown"
                }`}
              >
                {primaryContact.allegiance}
              </strong>
            </div>
            <div className="radar-primary-field">
              <span>Range</span>
              <strong>{formatRangeForContact(primaryContact)}</strong>
            </div>
            <div className="radar-primary-field">
              <span>Bearing</span>
              <strong>{formatBearing(primaryContact.bearing_deg)}</strong>
            </div>
            <div className="radar-primary-field">
              <span>Heading</span>
              <strong>{formatBearing(primaryContact.heading_deg)}</strong>
            </div>
            <div className="radar-primary-field">
              <span>Speed</span>
              <strong>{formatSpeed(primaryContact.speed_kts)}</strong>
            </div>
            <div className="radar-primary-field">
              <span>TTI</span>
              <strong>{computeTTILabel(primaryContact)}</strong>
            </div>
            <div className="radar-primary-field">
              <span>Type</span>
              <strong>{describeContactType(primaryContact)}</strong>
            </div>
          </div>
        ) : (
          <p className="muted">No contacts in range.</p>
        )}
      </section>

      <section className="radar-panel radar-scope">
        <h3>Scope</h3>
        {radarFilteredContacts.length ? (
          <table className="radar-table">
            <thead>
              <tr>
                <th className="num">ID</th>
                <th>Status</th>
                <th>Type</th>
                <th>Label</th>
                <th>Grid</th>
                <th className="num">Bearing</th>
                <th className="num">Range</th>
                <th className="num">Speed</th>
                <th className="num">TTI</th>
                <th>Weapon</th>
                <th className="lock-col">Lock</th>
              </tr>
            </thead>
            <tbody>
              {radarFilteredContacts.map((contact) => {
                const isLocked = lockedContactId != null && contact.id === lockedContactId;
                const buttonLabel = isLocked
                  ? radarLockPending === "unlock"
                    ? "Unlocking…"
                    : "Unlock"
                  : radarLockPending === contact.id
                  ? "Locking…"
                  : "Lock";
                return (
                  <tr key={contact.id} className={isLocked ? "locked-row" : undefined}>
                    <td className="num">{contact.id}</td>
                    <td>
                      <span
                        className={`radar-tag ${
                          isHostile(contact) ? "hostile" : isFriendly(contact) ? "friendly" : "unknown"
                        }`}
                      >
                        {contact.allegiance}
                      </span>
                    </td>
                    <td>{describeContactType(contact)}</td>
                    <td>{contact.label}</td>
                    <td>{contact.cell}</td>
                    <td className="num">{formatBearing(contact.bearing_deg)}</td>
                    <td className="num">{formatRangeForContact(contact)}</td>
                    <td className="num">{formatSpeed(contact.speed_kts)}</td>
                    <td className="num">{computeTTILabel(contact)}</td>
                    <td>{contact.primary_weapon || "—"}</td>
                    <td className="lock-cell">
                      <button
                        type="button"
                        className="btn radar-lock-btn"
                        onClick={() => (isLocked ? unlockRadarContact() : lockRadarContact(contact.id))}
                        disabled={radarLockPending !== null}
                      >
                        {buttonLabel}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="muted">No tracks to display with current filters.</p>
        )}
      </section>
    </div>
  );

  const stationContent =
    activeStation === "NAV" ? navContent : activeStation === "RADAR" ? radarContent : weaponsContent;

  return (
    <main className="nav-console">
      {stationTabs}
      {stationContent}
    </main>
  );
}
