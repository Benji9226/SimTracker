# SimTracker

Lap analysis for **Le Mans Ultimate**. It reads the telemetry the game already saves, so there is nothing to
configure: the Telemetry folder is found automatically and new sessions appear on their own.

## Run it

    pip install duckdb pandas numpy matplotlib

    python dashboard.py        # opens http://localhost:8765

Two pages:

| Page | What it is for |
|------|----------------|
| **Compare laps** | Click one of your laps and see how it differs from your best lap: time gap, speed, gear, throttle, brake and steering, plus how much time you gained or lost in each turn. |
| **What to practise** | Looks at all your clean laps together: your *ideal lap* (best time through every turn added up) and the three turns where you lose the most time by being inconsistent. Your saved laps from other days count as extra tries at every turn (there's a tick box to leave them out). |

A *clean lap* is a full lap without a pit visit **that the game counted**. The game gives no lap time to a lap with
a track-limits penalty, so such a lap is marked *invalid*: it is never your best lap, never saved as your personal
best, and left out of the comparisons and the practise numbers. Lap times are the game's own, so they match
what you saw in the game. Everything is local: nothing is sent anywhere.

### Saved laps

Your **personal best** for each track and car is saved automatically, and replaced whenever you set a faster
clean lap (the one it replaces is kept in a `history` folder, so nothing is lost). On **Compare laps** you can
pick it under *Compare it against*, so tomorrow's laps can be measured against today's best. To keep a lap
that isn't your best, click **Save this lap**; it stays until you delete it (*My saved laps*).

Saved laps live in `data/saved_laps/<track>/<car>/`.

### Automatic clean-up of old telemetry

The game writes a new session file for every session and never removes them. So that they don't pile up, the
dashboard deletes session files that haven't been written to for **30 days**: when it starts, and then once a day
while it runs. Change the number in `data/settings.json` (`telemetry_cleanup_days`; `0` = never delete anything).

What it will and won't do:
- It only touches `.duckdb` session files in the game's Telemetry folder, never `config.json` or anything else.
- It always keeps your newest session, however old.
- Before a session goes, its best lap is checked against your saved personal best, so a record isn't lost.
  Your saved laps (`data/saved_laps`) are never deleted.
- A file the game has open is left alone. Everything it deletes is listed in `data/cleanup_log.txt`.
- Deleted sessions are gone for good (no recycle bin), so their lap-by-lap history can no longer be opened.

The footer of each page says what it is set to, how big the telemetry folder is, and what the last check did.

### Track layout drawing

At the top left of **Compare laps** a card shows the layout of the circuit you're driving, drawn in the page's own
colours. The drawings are stored in `data/track_maps/` (one small file per circuit), so nothing is fetched while the
app runs. They were redrawn once from free maps on Wikimedia Commons; the authors and licences are listed in
`data/track_maps/CREDITS.md` and shown under each map. The drawing is picked from the circuit and the length of your
laps (through the track catalog), so a different layout of the same circuit shows no map instead of a wrong one.
There is no map for Daytona, Bahrain's Endurance layout or Paul Ricard with the chicane.

**Hover dot**: when you move the mouse over the charts, a dot on the map shows where on the track that point of the
lap is. To do this the app lays your lap's GPS shape (the game's own coordinates: not real-world ones, but the shape
is true) over the map, trying every start point and both directions, and keeps the best fit (code in
`simtracker/analysis/layout.py`). It is checked automatically: a poor fit shows no dot rather than a wrong one. No dot
for the three maps drawn as a filled band (COTA, Paul Ricard, Road Atlanta), or when a session file has no GPS.

### Track profiles

The first time you drive a track, its turns are found on your fastest clean lap and saved in
`data/tracks/<track>.json`. Every later session reuses that file, so Turn 5 is always the same place.
**Click a turn name** on either page to rename it; names are saved with the track. Delete the profile file to
have the turns found again (do that if the turns look wrong).

### The track catalog

`data/track_catalog.json` lists the official length and number of turns for every circuit in the game (Monza 11,
Spa 19, Le Mans 38, and so on). The figures were read once from the game's own website (lemansultimate.com/circuits)
and are stored in this file, so the app never goes online. When a track's turns are first found, they are checked
against it. If exactly one or two corners are missing (for example Pouhon at Spa looks like one long turn), the turn
that most looks like two is split automatically. The practise page says whether the count matches. An entry is only
used when your lap length matches the circuit (within 4%; the closest entry wins), so a short layout (like Sebring's
School Circuit) is never compared against the full circuit.

The website gives counts, not a numbered list of corner names, so turn names exist only for Monza for now.

A track that isn't in the catalog works too: its turns are found from your laps and just can't be checked.
To add or correct a track, edit the file (`confidence: "high"` allows automatic fixing, `"medium"` only shows
the count). The turn list is never *joined* automatically, only split.

There is also a plain text version for the terminal:

    python tracker.py                 # newest session
    python tracker.py --watch         # re-run whenever the game saves
    python tracker.py --list          # recent sessions
    python tracker.py --laps 3 5      # compare lap 5 against lap 3

## Folder layout

    SimTracker/
    ├─ dashboard.py          start the web dashboard
    ├─ tracker.py            start the text report
    │
    ├─ simtracker/           the Python code
    │  ├─ paths.py             where the folders below are
    │  ├─ server.py            the small local web server behind the dashboard
    │  ├─ cli.py               the text report (tracker.py)
    │  ├─ storage/             what SimTracker remembers between sessions
    │  │  ├─ profiles.py         track profiles: where the turns are, and their names
    │  │  └─ saved_laps.py       personal best (automatic) and laps you save yourself
    │  ├─ telemetry/           getting data out of the game
    │  │  ├─ finder.py           finds the Telemetry folder, lists files
    │  │  ├─ loader.py           reads a .duckdb file into one table
    │  │  ├─ sessions.py         joins the files of one game session (the game starts a new file each run)
    │  │  ├─ cleanup.py          deletes old session files to save disk space
    │  │  └─ info.py             car, class and track of a session
    │  └─ analysis/            the maths
    │     ├─ laps.py             lap times, clean laps, comparing two laps
    │     ├─ corners.py          finds the turns on a lap and measures each one
    │     ├─ reconcile.py        splits a turn that is probably two, when the official count says one is missing
    │     ├─ consistency.py      ideal lap and per-turn consistency
    │     └─ report.py           text report and picture
    │
    ├─ web/                  what you see in the browser
    │  ├─ laps.html            Compare laps page
    │  ├─ turns.html           What to practise page
    │  ├─ common.js / .css     shared header and styling
    │  └─ plotly.min.js        charts (bundled, so it works offline)
    │
    ├─ data/                 things you can edit, and things SimTracker remembers
    │  ├─ tracks/              one profile per track (turn positions and names), made automatically
    │  ├─ track_maps/          the layout drawings, with CREDITS.md (authors and licences)
    │  ├─ saved_laps/          your saved laps: <track>/<car>/best.json and pin-*.json
    │  ├─ track_catalog.json   official turn counts per circuit, used to check the turns found
    │  ├─ cars.json            optional: force a car model name for a team entry
    │  ├─ settings.json        your settings (how many days of telemetry to keep)
    │  └─ config.json          written automatically: where your Telemetry folder is
    │
    ├─ demo/                 demo.py runs the analysis on made-up data
    ├─ tools/                inspect_lmu.py prints the layout of a telemetry file
    ├─ docs/                 lmu_schema.txt, the layout of a real LMU file
    └─ output/               pictures from the text report

## Good to know

- **Turn names**: a new track starts as "Turn 1, Turn 2 ..." unless its catalog entry has `names` (Monza does; the
  count must match the turns found). After that, rename turns on the page.
- **Any track length**: laps are compared at a point every ~6 m, so a 13 km lap (Le Mans) is as detailed as a 5 km one.
- **A new session that can't be opened yet**: when you enter a session the game keeps its file locked for a
  moment while it sets it up. The pages then keep showing the last session that worked, tell you so at the top, and
  check again every few seconds. They switch to the new session by themselves as soon as it opens.
- **Car model**: the game only saves the team entry in the telemetry file. The model is looked up from the
  game's own results files (`UserData/Log/Results`); `data/cars.json` can override it.
- **Different Telemetry folder?** Set the `LMU_TELEMETRY_DIR` environment variable, or edit `data/config.json`.
- **Turns are found from the steering trace** of your fastest lap: every stretch where the wheel is held one
  way is a turn, so a chicane is two turns.
