# falklands/data/contacts_catalog.py
# Master spawn catalog for radar contacts.
# Fields:
#   name: display name for radio reports
#   status: Friendly | Neutral | Unknown | Hostile | Urgent threat
#   armament: short note for flavor / future logic
#   weight: higher = more likely to spawn
#   group: motion/behavior class -> "surface" | "air" | "missile" | "unknown"
#
# NOTE: Clock/bearing is NEVER taken from here. It’s computed each tick from geometry.
# This catalog only drives *what* spawns and how often.

CATALOG = [
    {"name": "Fishing trawler",            "status": "Neutral",        "armament": "None",                               "weight": 7, "group": "surface"},
    {"name": "Cargo freighter",            "status": "Neutral",        "armament": "None",                               "weight": 6, "group": "surface"},
    {"name": "Civilian yacht",             "status": "Neutral",        "armament": "None",                               "weight": 5, "group": "surface"},
    {"name": "Cruise liner",               "status": "Neutral",        "armament": "None",                               "weight": 3, "group": "surface"},
    {"name": "Oil tanker",                 "status": "Neutral",        "armament": "None",                               "weight": 4, "group": "surface"},
    {"name": "Passenger aircraft",         "status": "Neutral",        "armament": "None",                               "weight": 5, "group": "air"},
    {"name": "Commercial airliner",        "status": "Neutral",        "armament": "None",                               "weight": 5, "group": "air"},
    {"name": "Patrol boat",                "status": "Unknown",        "armament": "Light machine guns",                 "weight": 3, "group": "surface"},
    {"name": "Fast motorboat",             "status": "Unknown",        "armament": "Possible light arms",                "weight": 3, "group": "surface"},
    {"name": "Helicopter (civilian)",      "status": "Neutral",        "armament": "None",                               "weight": 4, "group": "air"},
    {"name": "Unknown radar echo",         "status": "Unknown",        "armament": "Unknown",                            "weight": 6, "group": "unknown"},
    {"name": "Weather balloon",            "status": "Neutral",        "armament": "None",                               "weight": 2, "group": "air"},
    {"name": "Fishing vessel (cluster)",   "status": "Neutral",        "armament": "None",                               "weight": 5, "group": "surface"},
    {"name": "Naval frigate",              "status": "Hostile",        "armament": "Missiles, guns",                     "weight": 2, "group": "surface"},
    {"name": "Submarine (surfaced)",       "status": "Hostile",        "armament": "Torpedoes, SAM",                     "weight": 2, "group": "surface"},
    {"name": "Jet fighter",                "status": "Hostile",        "armament": "Air-to-air missiles, cannon",        "weight": 2, "group": "air"},
    {"name": "Bomber aircraft",            "status": "Hostile",        "armament": "Bomb payload",                       "weight": 1, "group": "air"},
    {"name": "Missile contact",            "status": "Urgent threat",  "armament": "High-explosive missile",             "weight": 1, "group": "missile"},
    {"name": "Drone (surveillance)",       "status": "Unknown",        "armament": "None / light surveillance",          "weight": 3, "group": "air"},
    {"name": "Drone (armed)",              "status": "Hostile",        "armament": "Missiles",                           "weight": 1, "group": "air"},
    {"name": "Smuggling vessel",           "status": "Unknown",        "armament": "Small arms",                         "weight": 2, "group": "surface"},
    {"name": "Civilian ferry",             "status": "Neutral",        "armament": "None",                               "weight": 4, "group": "surface"},
    {"name": "Fishing skiff",              "status": "Neutral",        "armament": "None",                               "weight": 3, "group": "surface"},
    {"name": "Naval destroyer",            "status": "Hostile",        "armament": "Heavy missiles, guns",               "weight": 1, "group": "surface"},
    {"name": "Coast Guard cutter",         "status": "Friendly",       "armament": "Light defensive arms",               "weight": 3, "group": "surface"},
    {"name": "Allied frigate",             "status": "Friendly",       "armament": "Standard naval armament",            "weight": 3, "group": "surface"},
    {"name": "Reconnaissance aircraft",    "status": "Hostile",        "armament": "Sensors, possible missiles",         "weight": 2, "group": "air"},
    {"name": "Merchant vessel (suspicious)","status": "Unknown",       "armament": "Concealed cargo possible",           "weight": 3, "group": "surface"},
    {"name": "Cargo aircraft",             "status": "Neutral",        "armament": "None",                               "weight": 3, "group": "air"},
    {"name": "Fast jet (friendly)",        "status": "Friendly",       "armament": "Air-to-air missiles",                "weight": 2, "group": "air"},
]