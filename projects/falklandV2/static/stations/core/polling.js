import { createPoller, fetchWithTimeout, STATUS_HEADERS } from './api.js';

export const POLL_DEFAULT_INTERVAL_MS = 1500;
export const POLL_MAX_INTERVAL_MS = 12000;
export const POLL_TIMEOUT_MS = 10000;

function ensureConnectionBanner() {
  let banner = document.getElementById('connection-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'connection-banner';
    banner.className = 'connection-banner hidden';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    banner.innerHTML = '<span class="connection-dot">●</span><span class="connection-text">Connecting…</span>';
    document.body.appendChild(banner);
  }
  return banner;
}

function setConnectionBanner(state, message) {
  const banner = ensureConnectionBanner();
  if (state === 'ok') {
    banner.classList.add('hidden');
    banner.classList.remove('warn', 'error');
    banner.setAttribute('aria-hidden', 'true');
    return;
  }
  banner.classList.remove('hidden');
  banner.classList.toggle('warn', state === 'warn');
  banner.classList.toggle('error', state === 'error');
  banner.setAttribute('aria-hidden', 'false');
  const dot = banner.querySelector('.connection-dot');
  const textNode = banner.querySelector('.connection-text');
  if (dot) {
    dot.textContent = state === 'error' ? '⨂' : state === 'warn' ? '◐' : '●';
  }
  if (textNode) {
    textNode.textContent = message || (state === 'warn' ? 'Connection hiccup — retrying…' : 'Connection lost — retrying…');
  }
}

async function fetchStationStatus(opts = {}) {
  const headers = Object.assign({}, STATUS_HEADERS);
  const options = { cache: 'no-store', headers };
  if (opts && opts.signal) options.signal = opts.signal;
  const response = await fetchWithTimeout('/api/status', options, POLL_TIMEOUT_MS);
  if (!response.ok) {
    const err = new Error(`status ${response.status}`);
    err.status = response.status;
    err.response = response;
    throw err;
  }
  return await response.json();
}

export function createStatusPolling({ onData, onError }) {
  let statusPoller = null;
  let legacyPollTimer = null;
  let lastOkTs = 0;
  let consecutiveErrors = 0;

  const handleSuccess = (data) => {
    consecutiveErrors = 0;
    lastOkTs = Date.now();
    setConnectionBanner('ok');
    if (typeof onData === 'function') {
      onData(data);
    }
  };

  const handleFailure = (err, ctx) => {
    if (ctx && typeof ctx.consecutiveErrors === 'number') {
      consecutiveErrors = ctx.consecutiveErrors;
    } else {
      consecutiveErrors += 1;
    }
    const sinceOk = lastOkTs ? Math.round((Date.now() - lastOkTs) / 1000) : null;
    const msg = sinceOk !== null && sinceOk > 5
      ? `Connection lost ${sinceOk}s ago — retrying…`
      : 'Connection hiccup — retrying…';
    setConnectionBanner(consecutiveErrors > 2 ? 'error' : 'warn', msg);
    if (typeof onError === 'function') {
      onError(err, { consecutiveErrors, lastOkTs, message: msg });
    }
  };

  const ensurePoller = () => {
    if (statusPoller || !createPoller) {
      return statusPoller;
    }
    statusPoller = createPoller({
      name: 'stations-status',
      intervalMs: POLL_DEFAULT_INTERVAL_MS,
      maxIntervalMs: POLL_MAX_INTERVAL_MS,
      backoffFactor: 1.6,
      timeoutMs: POLL_TIMEOUT_MS,
      request: ({ signal }) => fetchStationStatus({ signal }),
      onSuccess: (data) => handleSuccess(data),
      onError: (err, ctx) => handleFailure(err, ctx || { consecutiveErrors: consecutiveErrors + 1 })
    });
    return statusPoller;
  };

  const startLegacy = () => {
    if (legacyPollTimer) {
      clearInterval(legacyPollTimer);
      legacyPollTimer = null;
    }
    const legacyTick = async () => {
      try {
        const data = await fetchStationStatus();
        handleSuccess(data);
      } catch (err) {
        handleFailure(err, { consecutiveErrors: consecutiveErrors + 1 });
      }
    };
    legacyTick();
    legacyPollTimer = setInterval(legacyTick, POLL_DEFAULT_INTERVAL_MS);
  };

  const start = () => {
    const poller = ensurePoller();
    if (poller) {
      lastOkTs = Date.now();
      poller.start(true);
      return;
    }
    startLegacy();
  };

  const force = async () => {
    const poller = statusPoller || ensurePoller();
    if (poller) {
      try {
        await poller.force();
      } catch (_) { /* ignore */ }
      return;
    }
    try {
      const data = await fetchStationStatus();
      handleSuccess(data);
    } catch (err) {
      handleFailure(err, { consecutiveErrors: consecutiveErrors + 1 });
    }
  };

  return {
    start,
    force,
    getState: () => ({ consecutiveErrors, lastOkTs })
  };
}
