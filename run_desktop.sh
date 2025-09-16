#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "[ERROR] .venv not found. Create it and install requirements first." >&2
  echo "        python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

export PYTHONPATH=.
export PORT="${PORT:-5055}"
export FULLSCREEN="${FULLSCREEN:-1}"
export TOUCH="${TOUCH:-1}"

# Ensure server is up for the desktop HTTP client
if ! lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
  echo "→ Starting web server on $PORT"
  nohup ./run_falkland.sh >/dev/null 2>&1 &
  sleep 1
fi

# Qt plugin path fix (macOS): discover PySide6 plugins dir and export
PLUGINS_DIR="$(python - <<'PY'
import os
try:
    from PySide6.QtCore import QLibraryInfo
    try:
        p = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    except Exception:
        # Fallback: site-packages layout
        import PySide6
        p = os.path.join(os.path.dirname(PySide6.__file__), 'Qt', 'plugins')
    print(p, end='')
except Exception:
    print('', end='')
PY
)"
if [[ -n "$PLUGINS_DIR" && -d "$PLUGINS_DIR" ]]; then
  export QT_PLUGIN_PATH="$PLUGINS_DIR"
  if [[ -d "$PLUGINS_DIR/platforms" ]]; then
    export QT_QPA_PLATFORM_PLUGIN_PATH="$PLUGINS_DIR/platforms"
  fi
fi
# Fallback to Homebrew Qt6 if pip wheel path fails
if [[ ! -f "${QT_QPA_PLATFORM_PLUGIN_PATH:-}/libqcocoa.dylib" ]]; then
  if command -v brew >/dev/null 2>&1; then
    QT_BREW_PREFIX="$(brew --prefix qt@6 2>/dev/null || brew --prefix qt 2>/dev/null || true)"
    if [[ -n "$QT_BREW_PREFIX" ]]; then
      export QT_PLUGIN_PATH="$QT_BREW_PREFIX/lib/qt6/plugins"
      export QT_QPA_PLATFORM_PLUGIN_PATH="$QT_PLUGIN_PATH/platforms"
      export DYLD_FRAMEWORK_PATH="$QT_BREW_PREFIX/lib"
      export DYLD_LIBRARY_PATH="$QT_BREW_PREFIX/lib"
    fi
  fi
fi

# Qt frameworks path (macOS): help the Cocoa platform plugin resolve Qt*.framework
QT_LIB_DIR="$(python - <<'PY'
import os
try:
    from PySide6.QtCore import QLibraryInfo
    try:
        p = QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath)
    except Exception:
        import PySide6
        p = os.path.join(os.path.dirname(PySide6.__file__), 'Qt', 'lib')
    print(p, end='')
except Exception:
    print('', end='')
PY
)"
if [[ -n "$QT_LIB_DIR" && -d "$QT_LIB_DIR" ]]; then
  export DYLD_FRAMEWORK_PATH="$QT_LIB_DIR"
  export DYLD_LIBRARY_PATH="$QT_LIB_DIR"
fi

export QT_QPA_PLATFORM="cocoa"

echo "→ Launching desktop app (PORT=$PORT, FULLSCREEN=$FULLSCREEN, TOUCH=$TOUCH)"
PY_BIN="${VIRTUAL_ENV:-}/bin/python"; [[ -x "$PY_BIN" ]] || PY_BIN="$(command -v python3 || command -v python)"
exec "$PY_BIN" -u -m projects.falklandV2.desktop_app "$@"
