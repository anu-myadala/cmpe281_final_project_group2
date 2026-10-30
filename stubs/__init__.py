"""Stand-ins for the C2 (vehicle and device management) and C3 (sound
intelligence and alert) components of the SCICP platform.

They serve the same REST contract C1 and the web app call (docs/API_NOTES.md)
from the seeded demo data: the vehicle, vehicle_rider, alert and alert_action
tables in MySQL and the audio_events and vehicle_telemetry collections in
MongoDB. A demo event generator keeps the cars moving and raises alerts.

Two ways to run them:

* inline: ``STUBS_INLINE=1`` makes the C1 API import ``stubs.app`` and mount
  ``c2_router`` and ``c3_router`` under /c2/api/v1 and /c3/api/v1; events go
  straight to the in-process EventBus;
* standalone: ``python -m stubs.standalone`` runs two FastAPI apps on :8002
  (C2) and :8003 (C3), each under /api/v1, and reaches C1 over HTTP
  (POST /internal/events with the service token).

The real C2 and C3 components replace this package without any change to C1:
only C2_BASE_URL / C3_BASE_URL (and the nginx upstreams) point elsewhere.
"""
