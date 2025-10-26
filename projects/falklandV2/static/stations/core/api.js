const ApiClient = (typeof window !== 'undefined' && window.ApiClient) ? window.ApiClient : null;

export const DEFAULT_FETCH_TIMEOUT = ApiClient && Number.isFinite(ApiClient.DEFAULT_TIMEOUT_MS)
  ? ApiClient.DEFAULT_TIMEOUT_MS
  : 4500;

export const fetchWithTimeout = (ApiClient && typeof ApiClient.fetchWithTimeout === 'function')
  ? (url, options = {}, timeoutMs = DEFAULT_FETCH_TIMEOUT) => ApiClient.fetchWithTimeout(url, options, timeoutMs)
  : async function (url, options = {}, timeoutMs = DEFAULT_FETCH_TIMEOUT) {
    let controller = null;
    let timer = null;
    try {
      if (typeof AbortController !== 'undefined' && !(options && options.signal)) {
        controller = new AbortController();
        timer = setTimeout(() => controller && controller.abort(), Math.max(500, Number(timeoutMs) || DEFAULT_FETCH_TIMEOUT));
      }
      const opts = Object.assign({}, options);
      if (controller) opts.signal = controller.signal;
      return await fetch(url, opts);
    } catch (err) {
      if (controller && err && err.name === 'AbortError') {
        const e = new Error(`timeout ${timeoutMs}ms ${url}`);
        e.name = 'TimeoutError';
        throw e;
      }
      throw err;
    } finally {
      if (timer) clearTimeout(timer);
    }
  };

export const fetchJson = (ApiClient && typeof ApiClient.fetchJson === 'function')
  ? (url, options = {}, timeoutMs = DEFAULT_FETCH_TIMEOUT) => ApiClient.fetchJson(url, options, timeoutMs)
  : async function (url, options = {}, _timeoutMs = DEFAULT_FETCH_TIMEOUT) {
    const res = await fetchWithTimeout(url, options, DEFAULT_FETCH_TIMEOUT);
    if (!res.ok) throw new Error(`${res.status} ${url}`);
    return await res.json();
  };

export const createPoller = ApiClient && typeof ApiClient.createPoller === 'function'
  ? (cfg) => ApiClient.createPoller(cfg)
  : null;

export const STATUS_SCHEMA_VERSION = '1.0.0';

export const STATUS_HEADERS = (() => {
  const headers = {};
  if (typeof window !== 'undefined' && window.__stations_build) {
    headers['X-Stations-Build'] = String(window.__stations_build);
  }
  headers['X-Stations-Schema'] = STATUS_SCHEMA_VERSION;
  return headers;
})();
