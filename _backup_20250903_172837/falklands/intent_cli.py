# projects/falklands/intent_cli.py
from typing import List, Tuple, Dict
from openai import OpenAI
import re, json

# GPT prompt with simple command markers (no backticks)
SYSTEM_PROMPT = """
You are Ensign Jim Henson aboard HMS Coventry (South Atlantic, 1982).
Clipped Royal Navy radio traffic; address the CO as "Captain". Default station: Radar.
You interpret the Captain's words and, if appropriate, emit slash commands.

OUTPUT RULES:
1) First: the short in-character radio reply (max 2 short lines). End with "Over." unless you asked a question.
2) If actions are required, include a command block exactly between these tags (no extra text in the block):
[COMMANDS]
/weapons arm
/nav set heading=210 speed=20
[/COMMANDS]

If no actions are needed, omit the [COMMANDS] block entirely.
Do not explain the commands; keep the radio reply crisp.
"""

# Extract commands between [COMMANDS] ... [/COMMANDS]
COMMANDS_BLOCK = re.compile(r"\[COMMANDS\](.*?)\[/COMMANDS\]", re.DOTALL | re.IGNORECASE)

def extract_commands(text: str) -> List[str]:
    m = COMMANDS_BLOCK.search(text)
    if not m:
        return []
    body = m.group(1)
    cmds: List[str] = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("/"):
            cmds.append(line)
    return cmds

def ask_ensign(client: OpenAI, user_text: str, state: Dict) -> Tuple[str, List[str]]:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content":
            "Captain said: " + user_text + "\n\n" +
            "HUD (summary): " + json.dumps(state, ensure_ascii=False)
        },
    ]
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.3,
        messages=msgs,
    )
    text = resp.choices[0].message.content or ""
    return text, extract_commands(text)

def main():
    client = OpenAI()
    # Minimal fake state so the Ensign has context; adjust later if you want.
    state = {
        "ship": {"grid": "50-50", "heading_deg": 270, "speed_kn": 15},
        "contacts_count": 0,
        "primary": None
    }

    print("Ensign online. Type orders (empty line quits).")
    while True:
        try:
            line = input("You: ").strip()
        except EOFError:
            break
        if not line:
            break
        reply, cmds = ask_ensign(client, line, state)
        print("\nNPC:\n" + reply.strip())
        if cmds:
            print("\n[Commands]")
            for c in cmds:
                print(c)
        print()
    print("End.")

if __name__ == "__main__":
    main()