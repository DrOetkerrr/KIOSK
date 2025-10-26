export const ST = {
  active: 'NAV',
  test: false,
  nav: { desiredHeading: '', desiredSpeed: '' },
  wpn: { lockInput: '' },
  events: [],
  eventHistory: [],
  eventKeys: { launch: null, result: null, cap: null, capRecover: null },
  power: {},
  radar: {}
};

export const STATION_KEYS = ['NAV', 'RADAR', 'WPN', 'RADIO', 'ENG'];
export const STATION_LABELS = { NAV: 'NAV', RADAR: 'RDR', WPN: 'WPN', RADIO: 'COMMS', ENG: 'ENG' };
export const VOICE_DEVICE_STORAGE_KEY = 'voice_device_id';
export const POWER_STORAGE_KEY = 'station_power';
export const STATION_CONFIG = {
  NAV: { channel: 1, voice: 'Navigation' },
  RADAR: { channel: 2, voice: 'Radar' },
  WPN: { channel: 3, voice: 'Weapons' },
  RADIO: { channel: 4, voice: 'Pilot' },
  ENG: { channel: 5, voice: 'Engineering' },
  LOG: { channel: 4, voice: 'Bridge' },
  SYS: { channel: 4, voice: 'Bridge' }
};

export function stationLabel(key) {
  return STATION_LABELS[key] || key;
}

export function stationInfo(key) {
  const cfg = STATION_CONFIG[key];
  return cfg ? { channel: cfg.channel, voice: cfg.voice } : { channel: 4, voice: 'Bridge' };
}

export function loadStationPower() {
  if (!ST.power || typeof ST.power !== 'object') ST.power = {};
  let saved = {};
  try {
    const raw = localStorage.getItem(POWER_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) saved = parsed;
    }
  } catch (_) { saved = {}; }
  STATION_KEYS.forEach((key) => {
    if (typeof saved[key] === 'boolean') ST.power[key] = saved[key];
    else if (typeof ST.power[key] !== 'boolean') ST.power[key] = true;
  });
  saveStationPower();
}

export function saveStationPower() {
  try { localStorage.setItem(POWER_STORAGE_KEY, JSON.stringify(ST.power)); } catch (_) { }
}

export function isStationPowered(key) {
  if (!ST.power || typeof ST.power !== 'object') return true;
  if (!Object.prototype.hasOwnProperty.call(ST.power, key)) return true;
  return ST.power[key] !== false;
}
