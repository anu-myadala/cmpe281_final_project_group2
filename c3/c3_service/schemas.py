"""Response models of the C3 read API (Part III, Section 2.1, Table 83).

They are the contract with C1 and the web app, written down once: FastAPI checks
every response against them and publishes them in the OpenAPI docs (/api/v1/docs).
The fields are those the stub (stubs/c3.py) sends, a superset of Table 83, so the
golden tests can compare C3 and the stub field for field. Timestamps are ISO-8601
UTC strings (timeutil.iso).
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AlertRow(BaseModel):
    """One alert in the queue, in the map's detail, and in alert.created / the action answer."""
    alertId: int
    vehicleId: str                      # public vehicle code, e.g. AV-01
    type: str                           # alert type code, e.g. ACCIDENT
    severity: str                       # LOW | MEDIUM | HIGH | CRITICAL
    status: str                         # NEW | ACKNOWLEDGED | RESOLVED | FALSE_ALARM
    topClass: Optional[str] = None      # sound class name, e.g. crash
    confidence: Optional[float] = None
    createdAt: Optional[str] = None
    ageSeconds: Optional[int] = None    # seconds since createdAt, at the time of the answer
    acknowledgedBy: Optional[str] = None  # display name
    acknowledgedAt: Optional[str] = None
    closedBy: Optional[str] = None      # display name
    closedAt: Optional[str] = None
    vehicleState: Optional[str] = None  # MOVING | PARKED
    count: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    eventRef: Optional[str] = None      # _id of the audio_events document
    ruleId: Optional[int] = None
    modelVersion: Optional[str] = None
    updatedAt: Optional[str] = None


class AlertPage(BaseModel):
    """GET /alerts: one page of alerts; total is the number of matches before paging."""
    items: List[AlertRow]
    total: int
    page: int
    size: int


class MapPoint(BaseModel):
    """GET /alerts/map: one pin. C1 keeps alertId, vehicleId, lat, lon, severity, type and createdAt."""
    alertId: int
    vehicleId: str
    lat: float
    lon: float
    severity: str
    type: str
    status: str
    topClass: Optional[str] = None
    createdAt: Optional[str] = None


class EventInfo(BaseModel):
    """The audio event behind the alert (MongoDB audio_events, Table 96)."""
    eventRef: str
    eventId: Optional[str] = None
    ts: Optional[str] = None
    channel: Optional[str] = None       # interior | exterior
    deviceId: Optional[str] = None
    location: Optional[Dict[str, Any]] = None   # GeoJSON Point, coordinates [lon, lat]
    context: Optional[Dict[str, Any]] = None    # speedKph, driveMode, ignition
    topK: Optional[List[Dict[str, Any]]] = None  # [{class, conf}], highest first, at most 3
    modelVersion: Optional[str] = None
    latencyMs: Optional[int] = None
    edge: Optional[Dict[str, Any]] = None       # the edge pre-filter's guess
    decision: Optional[Dict[str, Any]] = None   # what C3 decided for this event


class RuleInfo(BaseModel):
    """The rule that fired (alert_rule, Table 94)."""
    model_config = ConfigDict(populate_by_name=True)

    ruleId: int
    scope: str                          # DEFAULT | TENANT
    alertType: str
    class_: Optional[str] = Field(default=None, alias="class")  # `class` is a Python keyword
    minConfidence: float
    vehicleState: str                   # ANY | MOVING | PARKED


class ClipInfo(BaseModel):
    """The 5 s clip. url stays null until C2's clip service signs one (the player is hidden then)."""
    clipId: Optional[str] = None
    s3Key: Optional[str] = None
    url: Optional[str] = None
    expiresAt: Optional[str] = None
    durationMs: Optional[int] = None
    sampleRate: Optional[int] = None
    note: Optional[str] = None


class ActionEntry(BaseModel):
    """One alert_action row. by/at are what the web reads; name/ts are the Table 83 names."""
    action: str                         # CREATE | ACKNOWLEDGE | RESOLVE | FALSE_ALARM
    userId: Optional[int] = None
    name: Optional[str] = None
    by: Optional[str] = None            # display name, or "system" for CREATE
    fromStatus: Optional[str] = None
    toStatus: Optional[str] = None
    note: Optional[str] = None
    ts: Optional[str] = None
    at: Optional[str] = None


class NotificationEntry(BaseModel):
    """One notification row: the user's email (or "dashboard" for the tenant room), DASHBOARD | EMAIL,
    PENDING | SENT | FAILED, and when it was sent."""
    recipient: str
    channel: str
    status: str
    sentAt: Optional[str] = None


class AlertDetail(BaseModel):
    """GET /alerts/{alertId}: everything the alert page needs, in one call."""
    alert: AlertRow
    event: Optional[EventInfo] = None
    rule: Optional[RuleInfo] = None
    clip: Optional[ClipInfo] = None
    actions: List[ActionEntry] = []
    notifications: List[NotificationEntry] = []


class ActionBody(BaseModel):
    """POST /alerts/{alertId}/actions. Defined exactly like the stub's, so a bad body gets the same 400 wording."""
    action: Literal["ACKNOWLEDGE", "RESOLVE", "FALSE_ALARM"]
    note: Optional[str] = Field(default=None, max_length=500)


# -- the internal result call (Part III, Section 2.3, Table 85) ------------------------------------
class Score(BaseModel):
    """One class of the result: {"class": "crash", "conf": 0.91}."""
    model_config = ConfigDict(populate_by_name=True)

    class_: str = Field(alias="class", min_length=1, max_length=32)
    conf: float = Field(ge=0.0, le=1.0)


class ResultBody(BaseModel):
    """What the classifier worker reports for an audio event it has already stored."""
    tenantId: str = Field(min_length=1, max_length=16)    # tenant code, e.g. tA
    vehicleId: str = Field(min_length=1, max_length=32)   # vehicle code, e.g. AV-01
    topK: List[Score] = Field(default_factory=list, max_length=3)
    modelVersion: str = Field(min_length=1, max_length=64)
    latencyMs: Optional[int] = Field(default=None, ge=0)
    error: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _top_k_or_error(self) -> "ResultBody":
        if not self.topK and not self.error:
            raise ValueError("topK is empty and no error is given")
        return self


class Decision(BaseModel):
    """The engine's decision; alertId, alertType and severity only when there is an alert."""
    alert: bool
    alertId: Optional[int] = None
    alertType: Optional[str] = None
    severity: Optional[str] = None
    reason: str   # ALERT_CREATED NO_RULE STATE_MISMATCH BELOW_THRESHOLD SERVICE_OFF REPEAT_PENDING DUPLICATE_RESULT ERROR_RECORDED


class ResultResponse(BaseModel):
    decision: Decision


# -- the alert rules API (Part III, Section 2.2, Table 84) ------------------------------------------
class RuleItem(BaseModel):
    """One effective rule, in the stub's field order (stubs/c3.py GET /alert-rules)."""
    model_config = ConfigDict(populate_by_name=True)

    ruleId: int
    scope: str                              # DEFAULT | TENANT
    tenantId: Optional[int] = None          # the override's tenant; null for a platform default
    alertType: str
    class_: str = Field(alias="class")
    vehicleState: str                       # ANY | MOVING | PARKED
    minConfidence: float
    severity: str                           # what an alert of this rule gets (override, else the type's default)
    severityOverride: Optional[str] = None
    minRepeats: int
    windowSeconds: Optional[int] = None
    enabled: bool
    soundClass: str                         # the same as class, for the web's rules table
    updatedBy: Optional[str] = None         # display name; null for the seeded defaults
    updatedAt: Optional[str] = None


class RuleChanges(BaseModel):
    """PUT /alert-rules/{ruleId}: only the fields sent are changed; severityOverride null removes the override.
    Unknown fields are refused (400), so a typo can't be silently ignored."""
    model_config = ConfigDict(extra="forbid")

    minConfidence: Optional[float] = None
    severityOverride: Optional[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]] = None
    minRepeats: Optional[int] = None
    windowSeconds: Optional[int] = None
    enabled: Optional[bool] = None

    def changes(self) -> Dict[str, Any]:
        """The fields the caller actually sent (explicit nulls included)."""
        return {name: getattr(self, name) for name in self.model_fields_set}
