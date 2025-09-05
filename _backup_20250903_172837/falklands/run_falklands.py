#!/usr/bin/env python3
import os, sys
from pathlib import Path
from falklands.core.engine import Engine

def main():
    if "OPENAI_API_KEY" not in os.environ:
        print("Set OPENAI_API_KEY in your environment.", file=sys.stderr)
        sys.exit(1)

    state_path = Path.home() / "kiosk" / "state_falklands.json"
    eng = Engine(state_path=state_path, model="gpt-4.1-mini")

    print("Falklands V2 — unified codebase. Type a message or a /command. (:reset to reload, :quit to exit)")
    while True:
        try:
            line = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye."); break

        if line == ":quit":
            print("Bye."); break

        if line == ":reset":
            eng.st = eng.st.__class__(state_path).load()
            eng.st.history.clear()
            eng.st.add_message("system", "Reloaded")
            eng._register_systems()
            eng.st.save()
            print("Context reset.")
            continue

        reply = eng.ask(line)
        print(f"\nNPC:\n{reply}")

if __name__ == "__main__":
    main()
