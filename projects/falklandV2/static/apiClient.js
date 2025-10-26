(function(global){
  if(typeof global === 'undefined') return;

  const DEFAULT_TIMEOUT_MS = 4500;

  function normalizeOptions(options){
    if(!options || typeof options !== 'object') return {};
    return options;
  }

  function fetchWithTimeout(url, options, timeoutMs){
    const opts = normalizeOptions(options);
    const timeout = Number.isFinite(timeoutMs) ? Number(timeoutMs) : DEFAULT_TIMEOUT_MS;
    if(typeof AbortController === 'undefined'){
      return fetch(url, opts);
    }
    const controller = new AbortController();
    const signal = controller.signal;
    const merged = Object.assign({}, opts, { signal });
    const timer = timeout > 0 ? setTimeout(() => {
      try{ controller.abort(); }catch(_){}
    }, Math.max(250, timeout)) : null;
    return fetch(url, merged).finally(() => {
      if(timer) clearTimeout(timer);
    });
  }

  async function fetchJson(url, options, timeoutMs){
    const res = await fetchWithTimeout(url, options, timeoutMs);
    let data = null;
    try{
      data = await res.json();
    }catch(_){
      data = null;
    }
    if(!res.ok){
      const err = new Error(`HTTP ${res.status}`);
      err.name = 'HttpError';
      err.status = res.status;
      err.response = res;
      err.data = data;
      throw err;
    }
    return data;
  }

  function createPoller(config){
    const cfg = config || {};
    const requestFn = typeof cfg.request === 'function'
      ? cfg.request
      : async function(opts){ return fetchJson(String(cfg.url || '/'), cfg.options || { cache:'no-store' }, cfg.timeoutMs); };
    const baseInterval = Math.max(0, Number.isFinite(cfg.intervalMs) ? cfg.intervalMs : 1500);
    const maxInterval = Math.max(baseInterval, Number.isFinite(cfg.maxIntervalMs) ? cfg.maxIntervalMs : baseInterval);
    const backoffFactor = Number.isFinite(cfg.backoffFactor) && cfg.backoffFactor > 1 ? cfg.backoffFactor : 1.6;
    const jitterRatio = Math.max(0, Number(cfg.jitterRatio || 0));
    const timeoutMs = Number.isFinite(cfg.timeoutMs) ? cfg.timeoutMs : 10000;
    const name = String(cfg.name || '');

    let timer = null;
    let abortController = null;
    let inFlight = false;
    let destroyed = false;
    let consecutiveErrors = 0;
    let intervalMs = baseInterval;
    let lastOkTs = 0;
    let currentPromise = null;
    const waiters = [];

    function schedule(delay){
      if(destroyed) return;
      if(timer){ clearTimeout(timer); }
      const ms = Math.max(0, Number.isFinite(delay) ? delay : intervalMs);
      timer = setTimeout(() => {
        const run = invoke('timer');
        if(run && typeof run.catch === 'function'){
          run.catch(()=>{});
        }
      }, ms);
    }

    function computeDelay(){
      if(intervalMs <= 0) return 0;
      if(jitterRatio <= 0) return intervalMs;
      const range = intervalMs * jitterRatio;
      return Math.round(intervalMs + (Math.random() * 2 - 1) * range);
    }

    async function execute(trigger){
      inFlight = true;
      abortController = typeof AbortController !== 'undefined' ? new AbortController() : null;
      const signal = abortController ? abortController.signal : undefined;
      let timeoutId = null;
      if(abortController && timeoutMs > 0){
        timeoutId = setTimeout(() => {
          try{ abortController.abort(); }catch(_){}
        }, timeoutMs);
      }
      let aborted = false;
      try{
        const result = await requestFn({ signal, trigger, attempt: consecutiveErrors + 1, consecutiveErrors });
        consecutiveErrors = 0;
        intervalMs = baseInterval;
        lastOkTs = Date.now();
        if(typeof cfg.onSuccess === 'function'){
          await cfg.onSuccess(result, { trigger, lastOkTs });
        }
        return result;
      }catch(err){
        if(err && err.name === 'AbortError'){
          aborted = true;
          consecutiveErrors = 0;
          intervalMs = baseInterval;
        }else{
          consecutiveErrors += 1;
          intervalMs = Math.min(maxInterval, Math.round(baseInterval * Math.pow(backoffFactor, consecutiveErrors)));
          if(typeof cfg.onError === 'function'){
            try{
              cfg.onError(err, { trigger, consecutiveErrors, lastOkTs });
            }catch(_){}
          }else if(name){
            try{
              console.warn(`[poller:${name}] error`, err);
            }catch(_){}
          }
        }
        throw err;
      }finally{
        if(timeoutId) clearTimeout(timeoutId);
        abortController = null;
        inFlight = false;
        if(typeof cfg.onFinally === 'function'){
          try{
            cfg.onFinally({ trigger, consecutiveErrors, lastOkTs, aborted });
          }catch(_){}
        }
        while(waiters.length){
          try{ waiters.shift()(); }catch(_){}
        }
        if(!destroyed){
          const nextDelay = aborted ? 0 : computeDelay();
          schedule(nextDelay);
        }
      }
    }

    function invoke(trigger){
      if(destroyed){
        return Promise.resolve();
      }
      if(inFlight){
        return currentPromise || Promise.resolve();
      }
      const run = execute(trigger);
      currentPromise = run.finally(() => { currentPromise = null; });
      return currentPromise;
    }

    function start(immediate = true){
      destroyed = false;
      consecutiveErrors = 0;
      intervalMs = baseInterval;
      lastOkTs = Date.now();
      if(timer){ clearTimeout(timer); timer = null; }
      if(immediate){
        const run = invoke('start');
        if(run && typeof run.catch === 'function'){ run.catch(()=>{}); }
      }else{
        schedule(baseInterval);
      }
    }

    function stop(){
      destroyed = true;
      if(timer){ clearTimeout(timer); timer = null; }
      if(abortController){
        try{ abortController.abort(); }catch(_){}
        abortController = null;
      }
    }

    function force(){
      destroyed = false;
      if(timer){ clearTimeout(timer); timer = null; }
      return new Promise((resolve) => {
        const runNow = () => {
          const run = invoke('force');
          if(run && typeof run.finally === 'function'){
            run.finally(resolve);
          }else{
            resolve();
          }
        };
        if(inFlight){
          if(abortController){
            try{ abortController.abort(); }catch(_){}
          }
          waiters.push(runNow);
        }else{
          runNow();
        }
      });
    }

    function state(){
      return {
        inFlight,
        consecutiveErrors,
        intervalMs,
        lastOkTs,
        destroyed
      };
    }

    return { start, stop, force, state };
  }

  const ApiClient = Object.assign({}, {
    DEFAULT_TIMEOUT_MS,
    fetchWithTimeout,
    fetchJson,
    createPoller
  }, global.ApiClient || {});

  global.ApiClient = ApiClient;
  if(typeof global.fetchWithTimeout !== 'function'){
    global.fetchWithTimeout = fetchWithTimeout;
  }
})(typeof window !== 'undefined' ? window : this);
