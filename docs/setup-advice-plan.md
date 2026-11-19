# Plan: a Setup page with advice

Status: **plan only, nothing built.** Written 2026-11-19 after looking at what the game records (McLaren 720S GT3 Evo sessions at
Spa, Monza, Bahrain, Sebring, Le Mans). Rules we work by: ask before building, no version bump until the feature is tested.

## 1. Goal

A page (advanced, next to *Compare laps* and *What to practise*) that
1. shows the setup the car was driven with, in readable groups, and what changed since the last session;
2. shows the conditions (weather, track temperature);
3. finds problems in the telemetry and says what to try in the setup, using the car's **current values** ("front anti-roll bar is at
   position 6; try one step in the direction that reduces understeer"), one change at a time.

It must stay beginner-friendly: plain words, few items, one suggestion at a time, "try this" not "do this".

## 2. What the game gives us

### The setup (every telemetry file has it)
`metadata.CarSetup` is JSON with 171 entries (159 active on the McLaren GT3). Each entry has:
`key`, `value` (index), `minValue`, `maxValue`, `stringValue` (what the game shows, e.g. `5.4 deg`, `136 kPa`, `P2 20x2mm`, `B14`),
`available` (false = "N/A" on this car), `isFreeSetting` (false = locked, e.g. fixed-setup races), `numChangesValue` and
`lastSavedStringValue` (how it differs from the saved setup file), `diffComparisonValue`.

Groups found on the GT3: tyres (compound, pressure per corner), alignment (camber and toe, front and rear), springs, ride heights,
dampers (slow/fast bump and rebound per corner), anti-roll bars, rear wing, brake bias, brake pressure, brake ducts, differential
preload, traction control (3 maps), ABS map, steering lock, engine mixture, radiators.
Hypercars and LMP2 have different (and more) entries, so the knowledge base is per car class.

Privacy: the same metadata also has `DriverName` and `SteamID`. They must never be copied into anything we store, export or show.

### Telemetry that matters (rate in Hz)
| Channel | Use |
|---|---|
| Wheel Speed, 4 wheels (100) | wheelspin (rear faster than ground speed), lock-up (front slower under braking), left/right difference |
| Tyre temps left/centre/right, 4 tyres (100); carcass (5); rubber (10); pressure (10); wear (10) | camber (inner vs outer), pressure (centre vs edges), balance front/rear |
| Brake temps (50), brake force per wheel (50), Brake Pos (50) | bias, lock-ups, cooling |
| Ride heights, suspension position (100) | bottoming, platform, wing/ride-height effects |
| G Force Lat / Long / Vert (10), Ground Speed (100), Steering Pos (100, **% of full lock**), steering shaft torque (100) | corner balance |
| TC (100, on/off), TCCut, TCSlipAngle, ABS (events) | traction control and ABS activity |
| Engine RPM, water/oil temp (7), Gear | engine, cooling |
| Track/ambient temperature, rain, humidity, wind (1) | conditions |
There is **no yaw-rate or slip-angle channel**, so understeer/oversteer cannot be read directly.

## 3. What we can detect, and how sure

| Finding | How | Confidence |
|---|---|---|
| Wheelspin / poor traction on exit | rear wheel speed vs front under throttle, TC events | high |
| Lock-ups, bias too far forward/back | front wheel speed vs ground speed in braking, ABS events, front vs rear brake temps | high |
| Tyre pressure, camber | temperature spread across each tyre, hot pressures, wear difference | high |
| Bottoming out | ride height / suspension travel at limits | high |
| Overheating (engine, oil, brakes) | temperatures | high |
| **Understeer / oversteer by corner and phase (entry, mid, exit)** | steering vs lateral g and speed, relative to the car's own normal behaviour; exit: rear wheel speed and TC | medium, needs real work |

A quick test (steering vs path curvature from GPS) was too noisy (R² 0.65-0.8, spikes); the real version must use the g-force
channels with proper filtering and a per-car baseline. Without yaw rate, results are "relative to your other corners", not absolute.

## 4. What the advice can and cannot say

Safe: anything tied to a number with a clear direction ("rear wing 5.4 deg", "pressure 136 kPa", "camber -1.5 deg", ride height cm,
brake bias %). The strings parse into numbers, so the direction is known.

Uncertain: items whose index direction the game does not state: anti-roll bars (`P2 20x2mm`, `P3 18x2mm`), springs (`5`),
dampers (`B14`, `R10`), differential preload, TC maps. For those we need a **small hand-checked table per car** (which way is stiffer / more
locked) that we confirm in the game, or we learn the direction from the user's own sessions (each session stores its setup).

Always: only suggest entries that are `available` and `isFreeSetting`; one change at a time; show confidence; the first line of every
suggestion says what evidence it is based on (the numbers and the corner); never promise a lap-time gain.

Knowledge base (symptom -> parameters), general vehicle dynamics, e.g. mid-corner understeer: softer front / stiffer rear anti-roll bar,
less front pressure/more camber (check temperatures first), rear ride height up, rear wing down. It must be checked against the actual car in the game.

## 5. The page

1. **Setup**: grouped like the game's menus, current value as the game shows it, changed-since-last-session marks, the conditions card.
2. **Health checks** (high-confidence rules above): a short list, each with the evidence and one suggested step.
3. **Symptom guide**: choose a corner and a symptom (understeer entry/mid/exit, oversteer entry/mid/exit, unstable under braking,
   no traction); get ranked ideas for the current setup, plus the telemetry that supports or contradicts the symptom.
4. **History** (later): setup of each session with its best lap and balance numbers, so the app can show what a change did.

## 6. Phases

1. **Viewer + conditions + health checks**: certain data only. Setup groups, diff between sessions, weather, tyre/brake/ride-height/cooling rules.
2. **Symptom guide** with the per-car knowledge table (McLaren GT3 first), checked in the game together.
3. **Automatic balance detection** per corner and phase, and learning from setup history.

## 7. Risks

- Direction of anti-roll bar, spring, damper and diff steps is not in the data (needs the checked table).
- Balance from steering/g-force is indirect; it will be noisy and must be shown as "likely", with the numbers.
- Setup entries and their meaning differ per car and per game update; unknown entries must be shown as-is, not interpreted.
- Fixed-setup sessions: setup entries are locked, so no advice there.
- Never store or share the driver's name or Steam ID from the metadata.

## 8. Open questions for the user

1. Cars: McLaren GT3 first, or several classes from the start?
2. Start with the symptom guide (reliable, you describe what the car does) or automatic detection (needs more work)?
3. Is a quick "worksheet" in the game (change one thing, drive 3 laps, the app reports what changed) wanted later?

---

## Appendix: can we see other drivers' laps in a multiplayer session? (researched 2026-11-19)

**Telemetry files (`UserData/Telemetry/*.duckdb`): only your own car.** Lap, Lap Time, Best LapTime, sector times and every channel
are for the driver. One channel looks outward: `Time Behind Next` (the gap to the next car, 1 Hz; it is 0 in a practice session
with nobody around). No lap times, lines or inputs of other drivers.

**Results files (`UserData/Log/Results/*.xml`): every driver in the session.** The file written for a session lists each driver with
car, class and number, final position, `BestLapTime`, and **every lap**: lap time, three sector times, elapsed time, position,
top speed, and tyre compound. (A practice file on this computer lists the player and three AI cars: 4 drivers, 22 laps in total.)
It also records `ConnectionType`, `Dedicated`, per-driver `Connected` and `ServerScored`, and incidents.
So **the fastest lap of the session and everybody's lap and sector times are available after the session**, as times only.
We already read this folder for the car model names. I could not verify a real multiplayer file here (none exists on this computer),
but the format is the same game engine's results file, and community result sites for LMU servers publish exactly these per-driver numbers.

**Not available in files: other drivers' telemetry** (inputs, speed trace, racing line). Third-party tools such as the
[Telemetry Tool for LMU](https://overtake.gg/downloads/telemetry-tool-for-lmu.73664) show comparisons live from the game while it runs; that
uses the game's live data interface, not the files, and would be a different, much larger feature.

What we could build from the results files: a session leaderboard, "your best vs the session best by sector", who was fastest where,
gap per sector, tyre compounds used. Caution: the files contain other drivers' names; show them only on the user's own screen and never in an
export.

Sources: [Telemetry Tool for LMU](https://overtake.gg/downloads/telemetry-tool-for-lmu.73664),
[SimRacingTelemetry (open-source example of lap/sector tables)](https://github.com/mcryan123/SimRacingTelemetry),
[community LMU server results page](https://friendspartyserverpy.duckdns.org/results/2024_12_11_22_8_PRACTICE).

### Live data from the game while it runs (researched 2026-11-19, from community projects; not tested here)

- LMU has a built-in **shared memory interface** (on Windows, switched on in the game's plugin settings; some tools say the old rFactor 2
  "Shared Memory Map" plugin DLL is also used). It has two buffers: **scoring** (about 5 Hz) and **telemetry** (up to about 50-100 Hz), both with
  **an entry for every active car** (up to 104), matched by vehicle id.
- Per car, scoring has: class, position, laps, lap distance, lap and sector times, gaps, pit status, driver and car names, flags.
- Per car, the telemetry structure has: position and orientation (x, y, z), velocity and acceleration, **throttle, brake, steering and clutch
  inputs (filtered and unfiltered)**, rpm, gear, fuel, four wheels with temperature, pressure, wear, brake temperature, tyre compounds.
- A local web API (127.0.0.1, port 6397; community-documented, not official) adds standings with sector times, pit and weather forecast data.
- **Not confirmed by anyone I could find:** whether the inputs and wheel data of *other human drivers in an online session* are filled in, or
  only their position, speed and scoring. In the offline captures others describe, only the player's telemetry was verified.
- To use it SimTracker would have to **record while the game runs** (read the buffers 10-50 times a second, cut each car into laps, save them) instead of
  reading the saved files afterwards. That is a new, larger part of the program, Windows only, and the structure layout changes between game versions.
- Caution: other drivers' names and data are personal; recording them is a decision for the user, and some leagues and servers may have rules about using
  opponents' live data. Check before using it in a competitive session.
- The cheap way to find out what an online session really gives: a small **read-only test** run during an online session that prints, for each car, which
  fields are non-zero. No recording, nothing saved.

Sources: [TinyPedal](https://github.com/TinyPedal/TinyPedal), [goLMUSharedMemory](https://github.com/LuisGasparGH/goLMUSharedMemory),
[rF2 Shared Memory Map plugin](https://github.com/TheIronWolfModding/rF2SharedMemoryMapPlugin),
[race-engineer: LMU integration notes](https://github.com/Alexander-Gro/race-engineer/blob/main/docs/03-LMU-INTEGRATION.md),
[go-lmu-api (REST bindings)](https://github.com/snipem/go-lmu-api), [lmu-pitwall](https://github.com/mattbeaudin/lmu-pitwall).
