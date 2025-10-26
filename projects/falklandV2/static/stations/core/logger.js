let appendConsole = null;

function ensureLogger(){
  if(appendConsole){
    return;
  }
  try{
    if(typeof window !== 'undefined' && typeof window.appendConsole === 'function'){
      const existing = window.appendConsole;
      appendConsole = function(message){
        try{
          existing.call(window, message);
        }catch(_){
          console.log(String(message));
        }
      };
      return;
    }
  }catch(_){
    appendConsole = null;
  }
  appendConsole = function(message){
    try{
      if(typeof window !== 'undefined'){
        const fn = window.appendConsole;
        if(typeof fn === 'function' && fn !== appendConsole){
          fn.call(window, message);
          return;
        }
      }
    }catch(_){ }
    try{
      console.log(String(message));
    }catch(_){ }
  };
  try{
    if(typeof window !== 'undefined' && typeof window.appendConsole !== 'function'){
      window.appendConsole = appendConsole;
    }
  }catch(_){ }
}

export function log(message){
  ensureLogger();
  try{
    appendConsole(String(message));
  }catch(_){
    try{ console.log(String(message)); }catch(__){}
  }
}
