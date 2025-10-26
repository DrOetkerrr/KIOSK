import { $ } from '../lib/dom.js';
import { ST } from '../core/state.js';

function eventTimestamp(){
  try{
    const tzOffset = -new Date().getTimezoneOffset();
    const now = new Date();
    const pad = (n)=> String(n).padStart(2,'0');
    const hours = pad(now.getHours());
    const minutes = pad(now.getMinutes());
    const seconds = pad(now.getSeconds());
    const sign = tzOffset >= 0 ? '+' : '-';
    const offsetHours = pad(Math.floor(Math.abs(tzOffset) / 60));
    const offsetMinutes = pad(Math.abs(tzOffset) % 60);
    return `${hours}:${minutes}:${seconds} ${sign}${offsetHours}:${offsetMinutes}`;
  }catch(_){
    return '—';
  }
}

export function formatEventTs(ts){
  try{
    if(ts===undefined || ts===null) return eventTimestamp();
    const num = Number(ts);
    if(!Number.isFinite(num)) return eventTimestamp();
    const date = new Date(num*1000);
    if(Number.isNaN(date.getTime())) return eventTimestamp();
    return new Intl.DateTimeFormat('en-US', {
      hour:'2-digit',
      minute:'2-digit',
      second:'2-digit'
    }).format(date);
  }catch(_){
    return eventTimestamp();
  }
}

export function renderEventConsole(){
  const box=$('#event-log');
  if(!box) return;
  box.innerHTML='';
  const list=Array.isArray(ST.events)? ST.events.slice().reverse(): [];
  if(!list.length){
    const row=document.createElement('div'); row.className='event-line muted';
    const label=document.createElement('div'); label.className='event-label'; label.textContent='No recent events';
    row.appendChild(label);
    box.appendChild(row);
    return;
  }
  list.forEach(function(ev){
    const row=document.createElement('div'); row.className='event-line';
    const label=document.createElement('div'); label.className='event-label'; label.textContent=ev.text || '';
    row.appendChild(label);
    if(ev.time){
      const tm=document.createElement('div'); tm.className='event-time'; tm.textContent=ev.time; row.appendChild(tm);
    }
    box.appendChild(row);
  });
}

export function pushEvent(kind, text){
  if(!Array.isArray(ST.events)) ST.events=[];
  ST.events.push({ kind, text, time: eventTimestamp() });
  if(ST.events.length>5) ST.events = ST.events.slice(-5);
  renderEventConsole();
}
