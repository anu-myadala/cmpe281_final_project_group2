"""AlertStateMachine: which action may move an alert from which status (BR-C3-07,
Part III, Section 4.4, Table 105; the state diagram of Section 3.4.1).

    NEW           --ACKNOWLEDGE-->  ACKNOWLEDGED
    NEW, ACKNOWLEDGED  --RESOLVE-->      RESOLVED      (final)
    NEW, ACKNOWLEDGED  --FALSE_ALARM-->  FALSE_ALARM   (final)

Anything else is refused with 409 CONFLICT and details {rule, from, to, allowed[]},
where allowed lists the actions that the current status does permit. The wording is
the stub's. Pure logic, no database: the service applies the result with a guarded
UPDATE.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from ..errors import Conflict

ACTIONS = ("ACKNOWLEDGE", "RESOLVE", "FALSE_ALARM")
# action -> (statuses it may start from, status it leads to); the order is the order of allowed[]
TRANSITIONS: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "ACKNOWLEDGE": (("NEW",), "ACKNOWLEDGED"),
    "RESOLVE": (("NEW", "ACKNOWLEDGED"), "RESOLVED"),
    "FALSE_ALARM": (("NEW", "ACKNOWLEDGED"), "FALSE_ALARM"),
}
FINAL_STATUSES = ("RESOLVED", "FALSE_ALARM")


class AlertStateMachine:
    @staticmethod
    def target(action: str) -> str:
        return TRANSITIONS[action][1]

    @staticmethod
    def allowed_actions(status: str) -> List[str]:
        return [action for action, (froms, _) in TRANSITIONS.items() if status in froms]

    @classmethod
    def check(cls, status: str, action: str) -> str:
        """The status `action` leads to from `status`; 409 CONFLICT (BR-C3-07) when it isn't allowed."""
        froms, to_status = TRANSITIONS[action]
        if status not in froms:
            raise Conflict("Action not allowed in this state",
                           {"rule": "BR-C3-07", "from": status, "to": to_status, "allowed": cls.allowed_actions(status)})
        return to_status
