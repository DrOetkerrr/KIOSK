export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => document.querySelectorAll(sel);
export const text = (el, s) => { if (el) el.textContent = s; };
export const fmt = (v, d) => (v === undefined || v === null || Number.isNaN(Number(v)))
  ? '—'
  : Number(v).toFixed(d || 0);
