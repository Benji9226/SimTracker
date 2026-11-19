# SimTracker

Lap analysis for **Le Mans Ultimate**. It reads the telemetry the game already saves, so there is nothing to
configure: the Telemetry folder is found automatically and new sessions appear on their own.

## Run it

**The easy way (Windows):** double-click `SimTracker.exe`. A black window opens (leave it open while you
use SimTracker, close it to stop) and the dashboard opens in your browser. Nothing to install. Your own files
(saved laps, settings, track profiles) are kept in a `SimTracker data` folder next to the exe. To give it to a
friend, send them just `SimTracker.exe`; they start with their own empty saved laps. Double-clicking it a
second time just reopens the page.

The Telemetry folder is found on its own, through Steam's own list of libraries, on any drive. If it ever
can't be found, a window asks you to pick the folder once and it is remembered.

Windows may show a *"Windows protected your PC"* warning the first time, because the exe is not signed.
Click *More info*, then *Run anyway*.

**Updating.** When SimTracker starts it looks at the project's latest release on GitHub (one small request to
GitHub, nothing about you or your laps is sent; offline it just says nothing). If the release is newer than the
version you have (shown in the page footer), an **Update application** button appears in the top right corner.
Pressing it closes the program, downloads the new `SimTracker.exe`, checks it, swaps it in and starts it again; the
open page reloads by itself. Your laps and settings (the `SimTracker data` folder) are never touched. It only works
in the double-click program, and only if the repository is public (a private repository cannot be read without
a login, so nobody sees an update).

**Publishing an update** (for the person who maintains it):

1. Raise `VERSION` in `simtracker/version.py` (for example `1.1.0`).
2. `python tools/build_exe.py`
3. On GitHub: *Releases* → *Draft a new release*, tag `v1.1.0` (the same number), attach `dist/SimTracker.exe`, publish.

Everyone running an older version then sees the button within a few hours of their next start.

**From source:**

    pip install duckdb pandas numpy            # (add matplotlib for the text report, tracker.py)

    python dashboard.py        # opens http://localhost:8765

**Making the exe** (after changing the code):

    pip install pyinstaller
    python tools/build_exe.py          # writes dist/SimTracker.exe (about 60 MB)

Two pages:

| Page | What it is for |
|------|----------------|
| **Compare laps** | Click one of your laps and see how it differs from your best lap: time gap, speed, gear, throttle, brake and steering, plus how much time you gained or lost in each turn. |
| **What to practise** | Looks at all your clean laps together: your *ideal lap* (best time through every turn added up) and the three turns where you lose the most time by being inconsistent. Your saved laps from other days count as extra tries at every turn (there's a tick box to leave them out). |

A *clean lap* is a full lap without a pit visit **that the game counted**. The game gives no lap time to a lap with
a track-limits penalty, so such a lap is marked *invalid*: it is never your best lap, never saved as your personal
best, and left out of the comparisons and the practise numbers. Lap times are the game's own, so they match
what you saw in the game. Everything is local: nothing is sent anywhere.

**Laps that don't count.** Out laps, pit laps and laps with track limits are always in the lap list, in grey, and
you can still compare them with any other lap (a corner may have been great in a lap that was thrown out). They only
never count as a best lap, can't be saved, and are left out of *What to practise*. A lap that is not a full lap
(the one you are still driving) is not listed. Tick *Hide laps that don't count* above the buttons if you want only your clean laps.

**Comparing with a friend.** On the *Compare laps* page, pick one of your laps and press **Export**: you are
asked for your name, and a small `.simlap` file is downloaded (lap time, car, track and the lap's speed, throttle,
brake, steering and gear along the track, and nothing else: no file paths, nothing about your computer). Send it
to your friend any way you like. They press **Import…** and pick the file, and they are
immediately comparing their lap against yours. Friends' laps are listed under *Friends' laps* in
the lap list (and in *Saved laps and friends' laps*, where you can delete them). Your own saved laps can be exported
from *Saved laps and friends' laps* too. A friend's lap belongs to the track, so it works whatever car either of you drove, but
it only appears when you open a session on the same track. It is never counted as one of your own tries in
*What to practise*. Lap files from other people are checked before they are kept.

**Sessions are grouped by day.** The game starts a new file every time you leave the garage. Files that carry on from each other
(same lap counter and odometer) are one session, and so are all runs from the **same day** (your own time zone) with the
**same track, layout, car and session type**, even when the game was restarted in between. Practice, qualifying and race
stay separate sessions. The laps of the runs are numbered one after another, so a day of practice is one list of laps.

**Session names.** The picker names each session from what the file says, newest first: start time (your own time
zone), circuit, session type, car, and how many runs it has, for example
`Wed 18 Nov 20:28 · Le Mans · Practice · McLaren 720S LMGT3 Evo`. Everyday circuit names come from the `short` field in
`data/track_catalog.json`; a circuit that is not in the catalog shows the game's own name.

**Deleting sessions.** *Manage sessions…* (next to the session picker) lists every telemetry session in the game's
folder with its size. Tick the ones you don't need (there are shortcuts for "older than 7 days" and "all but the
newest") and delete them. Your personal best is checked and kept first, and laps you saved are never touched.
A session the game still has open can't be deleted and is reported.

### Saved laps

Your **personal best** for each track and car is saved automatically, and replaced whenever you set a faster
clean lap (the one it replaces is kept in a `history` folder, so nothing is lost). On **Compare laps** you can
pick it in the lap list, so tomorrow's laps can be measured against today's best. To keep a lap
that isn't your best, click **Save lap**; it stays until you delete it (*Saved laps and friends' laps*).

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

### What SimTracker itself keeps (and its limits)

Nothing here grows without limit, however much you drive:

| What | Where | How big it can get |
|---|---|---|
| Track surface (the edges) | `data/track_surface/<track>.json` | **One file per track layout**, about 20-90 KB, whatever the number of laps. It stores running averages, not laps; once a point of the track has about 200 laps in it, a new lap only nudges the average (so it also follows a track that changes). It remembers which sessions it has used (a short entry each, the newest 400). |
| Personal best | `data/saved_laps/<track>/<car>/best.json` | One file per track and car; the laps it replaces go to `history/`, **newest 10 kept**, older ones deleted. |
| Laps you saved / friends' laps | same folders | Only what you save or import; you delete them yourself. |
| Turn names and positions | `data/tracks/<track>.json` | One small file per track. |

The only things that pile up are the game's own telemetry files, which the automatic clean-up above deletes after 30 days
(and *Manage sessions…* lets you delete by hand).

### Track layout drawing

At the top left of **Compare laps** a card shows the layout of the circuit you're driving, drawn in the page's own
colours. The drawings are stored in `data/track_maps/` (one small file per circuit), so nothing is fetched while the
app runs. They were redrawn once from free maps on Wikimedia Commons; the authors and licences are listed in
`data/track_maps/CREDITS.md` and shown under each map. The drawing is picked from the circuit and the length of your
laps (through the track catalog), so a different layout of the same circuit never gets the wrong drawing.
**When a layout has no drawing of its own** (the short Sebring School Circuit, Daytona, Bahrain's Endurance layout,
a circuit added to the game later), the map is drawn from your own fastest clean lap's GPS positions instead: the
shape is true, north is up, and the dot follows the lap exactly. It is made fresh each time (nothing is stored), and
it appears as soon as you have one clean lap.

**Hover dot**: when you move the mouse over the charts, a dot on the map shows where on the track that point of the
lap is. To do this the app lays your lap's GPS shape (the game's own coordinates: not real-world ones, but the shape
is true) over the map, trying every start point and both directions, and keeps the best fit (code in
`simtracker/analysis/layout.py`). It is checked automatically: when the fit is poor, that session's map is drawn from the
lap's own GPS instead of showing a wrong dot. The only case without a dot is a session file that has no GPS.

### Track view (racing lines)

Click the track card (or *Enlarge*) to open a big, zoomable map with the racing line of the reference lap (blue) and the lap
you are checking (red). **Scroll to zoom, drag to move, double-click to zoom out**; move the pointer over the track to read
both laps at that spot: distance, speed, brake and throttle, how far left/right of the track's centre line you were and how
much room you had to the track edge. *Colour the lines by* speed, braking, throttle or **room to the track edge** (red =
at the edge, green = 6 m or more to spare) shows where you brake, coast and how wide you take a corner. In the colour modes
the two laps are drawn as parallel strips that are kept at least a few pixels apart (the reference on the left, in the
direction of travel), so you can compare their colours even when zoomed out; zoom in and they move to where they really were.

The lines come from the game's GPS positions, which turn out to be accurate to well under a metre (they agree with the
game's own `Path Lateral` channel). The game also records where the nearest track edge is (`Track Edge`). From that, the big view draws the **track surface**
under the racing lines, so you can see how wide and how tight you take a corner. Each lap shows the edge on the side it
ran on; **what every lap saw is remembered per track** in `data/track_surface/<track>.json` (added when a session is
opened, and again just before old telemetry is deleted; a lap is only ever counted once), so the surface gets more
accurate the more you drive a track, across sessions and days. Where no lap has gone near an edge yet it is filled in
from the places that did, or put the usual width of that stretch away from the other edge. It can be switched off with
*Track surface*. Laps of another layout of the same circuit are kept apart (they have their own file).

The **start / finish line** is a green line across the track in the big view, and a green tick on the small map.

Every track gets a dot and a start/finish tick on the small map. COTA, Paul Ricard and Road Atlanta, which were drawn as a
thin filled outline of the road, are now stored as the centre line of that outline like the other maps. If a lap ever
does not fit a drawing, that session's map is drawn from the lap's own GPS instead, so there is always a dot.
Laps you save, export or import now carry their racing line, so you can compare against a saved lap or a friend's lap on
the map too. Laps saved by older versions have no line (your personal best gets one the next time its session is opened).

**Play the laps.** At the bottom of the big view, **Play** runs both laps in real time, like a race: they start together and
each goes at its own pace, so the faster lap pulls ahead and you can watch the gap build in the corners. Two dots (blue and
red) move along the track, and the two panels at the top follow them: each shows the lap's time and its own running clock,
a steering wheel that turns with the steering input (drawn as if full lock were about 270 degrees of wheel; the game gives
steering as a percentage of full lock), the brake and throttle pressure as bars, and the speed in km/h. The **gap between the laps** is a number beside the red dot on the map, always the same size however far you zoom:
how much later the checked lap reaches the spot where the reference is right now (+0.47 s = behind the reference, shown in red;
a minus sign and green = ahead). Pointing at a spot on the track shows the gap at that spot.
**Click a panel to make the map follow that lap**: its dot stays in the middle and you keep your own zoom; click the panel
again, or drag the map, to stop following. The slider jumps to any moment, and **Replay** starts again. When it is not
playing, pointing at the track shows the same panels for that spot. The *Steering* chart on the main page is now labelled
in % too (it said degrees, but the game's number is a percentage of full lock).

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
    ├─ dist/SimTracker.exe   the double-click program (made by tools/build_exe.py)
    │
    ├─ simtracker/           the Python code
    │  ├─ paths.py             where the folders below are
    │  ├─ server.py            the small local web server behind the dashboard
    │  ├─ cli.py               the text report (tracker.py)
    │  ├─ storage/             what SimTracker remembers between sessions
    │  │  ├─ profiles.py         track profiles: where the turns are, and their names
    │  │  └─ saved_laps.py       personal best (automatic) and laps you save yourself
    │  ├─ telemetry/           getting data out of the game
    │  │  ├─ finder.py           finds the Telemetry folder (Steam libraries), lists files
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
    ├─ tools/                build_exe.py makes SimTracker.exe (version: simtracker/version.py, update check: updater.py); inspect_lmu.py prints the layout of a telemetry file
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
