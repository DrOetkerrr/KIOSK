export interface ShipSnapshot {
  cell: string;
  x_nm: number;
  y_nm: number;
  heading_deg: number;
  speed_kts: number;
  hud: string;
}

export interface WeatherSnapshot {
  wind_dir_deg: number;
  wind_speed_kts: number;
  sea_state: number;
}

export interface RadioMessage {
  id: number;
  text: string;
  category: string;
  ts: number;
}

export interface NavCommandEntry {
  id: number;
  ts: number;
  action: string;
  value: number;
}

export interface CapHistoryEntry {
  id: number;
  ts: number;
  action: string;
  sorties: number;
  mission_status: string;
}

export interface RadarContact {
  id: number;
  label: string;
  allegiance: string;
  range_nm: number;
  bearing_deg: number;
  heading_deg: number;
  speed_kts: number;
  category?: string | null;
  primary_weapon?: string | null;
  cell: string;
}

export interface RadarSnapshot {
  contacts: RadarContact[];
  locked_contact_id: number | null;
}

export interface ShotInFlight {
  id: number;
  weapon: string;
  target: string;
  cell: string;
  range_nm: number;
  pk_pct: number;
  eta_s: number;
  result: string | null;
  mode: string;
}

export interface WaveSnapshot {
  label: string;
  elapsed_s: number;
  duration_s: number;
  remaining_s: number | null;
  spawn_rate_per_min: number;
  friendly_prob: number;
  direction_bearing: number;
}

export interface HealthAssetSnapshot {
  name: string;
  max_lives: number;
  lives: number;
}

export interface HealthSnapshot {
  assets: HealthAssetSnapshot[];
}

export interface MissionDecisionSnapshot {
  id?: string;
  prompt?: string;
  status?: string;
  choice?: string;
  timeout_s?: number;
}

export interface MissionSequenceSnapshot {
  index?: number | null;
  order?: Array<string | number> | null;
}

export interface MissionSettingsSnapshot {
  station_power_defaults?: Record<string, boolean> | null;
  stations_offline?: boolean;
  hostile_spawns?: boolean;
  [key: string]: unknown;
}

export interface MissionSnapshot {
  id: string;
  label: string;
  description: string;
  status: string;
  elapsed_s: number;
  time_left_s: number | null;
  decision: MissionDecisionSnapshot | null;
  sequence?: MissionSequenceSnapshot | null;
  settings?: MissionSettingsSnapshot | null;
  alert?: string | null;
  outcome?: string | null;
}

export interface SeaHarrierStatusSnapshot {
  callsign: string;
  status: string;
  fuel_pct: number;
  time_in_status_s: number;
}

export interface CapSnapshot {
  status: string;
  sorties: number;
  time_in_status_s: number;
  harriers: SeaHarrierStatusSnapshot[];
}

export interface AudioEventSnapshot {
  kind: string;
  message: string;
  ts: number;
}

export interface AudioSnapshot {
  events: AudioEventSnapshot[];
  shots_in_flight: ShotInFlight[];
}

export interface WeaponSlotSnapshot {
  name: string;
  state: string;
  ammo: number;
  max_ammo: number;
  min_range_nm: number | null;
  max_range_nm: number | null;
  supports: string[];
  ammo_per_shot: number;
  category: string;
  cooldown_remaining_s: number;
}

export interface WeaponsSnapshot {
  slots: WeaponSlotSnapshot[];
}

export interface StatusSnapshot {
  ship: ShipSnapshot;
  radar: RadarSnapshot;
  weather: WeatherSnapshot;
  radio: { messages: RadioMessage[] };
  nav_history: { entries: NavCommandEntry[] };
  cap_history: { entries: CapHistoryEntry[] };
  mission: MissionSnapshot;
  cap: CapSnapshot;
  wave: WaveSnapshot;
  health: HealthSnapshot;
  audio: AudioSnapshot;
  weapons: WeaponsSnapshot;
}
