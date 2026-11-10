# C2 component demo (Deliverable #3), about 7 minutes

Before recording: Docker Desktop running, then from the repository folder

```powershell
docker compose -f docker-compose.yml -f docker-compose.c2.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.c2.yml ps        # c2 healthy, c2-sim running
```

Wait about a minute so the simulated cars have sent some clips. Have two browser tabs: the C2 console
(http://localhost:8080/c2/console/, signed in as iot@scicp.example) and the C1 dashboard
(http://localhost:8080, signed in as maria@bayshuttle.example). Have a terminal open in the repository.

| # | Show | Say |
|---|---|---|
| 1 | Architecture slide or `docker compose ps` | C2 manages the cars: vehicles, devices, provisioning, settings, status, clips. It replaced the stub without changing C1: same API, now from real tables, MQTT and storage. |
| 2 | Console, tenant Bay Shuttle: AV-10..12 ONLINE, AV-13 OFFLINE, AV-14 never connected | Status comes from the cars' telemetry every 10 s; 30 s without it is OFFLINE (BR-C2-19). |
| 3 | C1 dashboard map: the shuttles moving | The map reads the telemetry C2 stores; nothing changed in C1. |
| 4 | Console, tenant Nguyen household: **+ Add vehicle** AV-02 (VIN 7SAYGDEE5PA000202, Tesla Model Y 2023) | Plan limit and VIN rules are checked (BR-C2-02, -03); C1 creates the default subscriptions. |
| 5 | Try AV-03 and AV-04: the 4th car is refused | BASIC plan allows 3 cars: 422 BR-C2-02. (Retire AV-03 afterwards or skip this step.) |
| 6 | AV-02 > **+ Add device** MIC_EXTERIOR, serial MIC-AV02-EXT | Device rules: one gateway, up to four microphones (BR-C2-05). |
| 7 | **Provision agent**, show the dialog, download agent.yaml | One login and one upload token per car, shown once (BR-C2-07..09). |
| 8 | Terminal: `python c2\edge\agent.py --config agent.yaml --mic` | My laptop is now car AV-02. Watch the console: AV-02 ACTIVE, devices ONLINE. |
| 9 | Clap or play a siren on the phone near the laptop; console Data tab of AV-02, **Play** | The pre-filter cut a 5 s clip (2 s before, 3 s after), uploaded it with the device token, and published the audio event for the classifier. Class says "pending" until the C4 worker classifies it. |
| 10 | Edit dev-0212: gain +12, **Apply** | 202: the setting is a command; it shows SENT, then "confirmed by the car" when the agent acks (BR-C2-13). Point at the agent's log line "SET_CONFIG dev-0212 -> APPLIED". |
| 11 | Stop the agent (Ctrl+C), change the threshold, Apply | The car is offline: the command stays PENDING and the status turns OFFLINE right away (last will). |
| 12 | Start the agent again | It reconnects, gets the pending setting and confirms it. |
| 13 | **Mute** the microphone, clap | A muted microphone sends nothing (BR-C2-15). Unmute. |
| 14 | Network tab of AV-12: add BayShuttle-Depot WPA2, Save | Passwords are encrypted, never shown again, sent only to that car (BR-C2-25). |
| 15 | Data tab: Delete a clip; set retention 30 days | The audio is removed at once; retention applies to new clips (BR-C2-23, -24). |
| 16 | Terminal: `tools/fetch_clip.py` (below) | The interface Ray's worker uses: the 5 s WAV for a clipId, service token only. |
| 17 | `cd c2; .venv\Scripts\python -m pytest -q` | 25 tests: the stub contract, tenancy, every decision table of Section 4. |

The worker's call (step 16), from the repository folder:

```powershell
docker compose -f docker-compose.yml -f docker-compose.c2.yml exec c2 python tools/fetch_clip.py             # 200, 5 s mono 16 kHz
docker compose -f docker-compose.yml -f docker-compose.c2.yml exec c2 python tools/fetch_clip.py --no-token  # 401
```
