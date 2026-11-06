"""BR-C3-07: every cell of the transition table (Part III, Table 105), 4 statuses x 3 actions.
An allowed cell gives the new status; a refused one the 409 body the web app shows."""
from __future__ import annotations

import pytest

from c3_service.errors import Conflict
from c3_service.services.alert_state import AlertStateMachine

OPEN_ACTIONS = ["RESOLVE", "FALSE_ALARM"]  # what ACKNOWLEDGED still allows

# (status, action) -> the new status, or ("409", allowed actions from that status)
CELLS = {
    ("NEW", "ACKNOWLEDGE"): "ACKNOWLEDGED",
    ("NEW", "RESOLVE"): "RESOLVED",
    ("NEW", "FALSE_ALARM"): "FALSE_ALARM",
    ("ACKNOWLEDGED", "ACKNOWLEDGE"): ("409", OPEN_ACTIONS),
    ("ACKNOWLEDGED", "RESOLVE"): "RESOLVED",
    ("ACKNOWLEDGED", "FALSE_ALARM"): "FALSE_ALARM",
    ("RESOLVED", "ACKNOWLEDGE"): ("409", []),
    ("RESOLVED", "RESOLVE"): ("409", []),
    ("RESOLVED", "FALSE_ALARM"): ("409", []),
    ("FALSE_ALARM", "ACKNOWLEDGE"): ("409", []),
    ("FALSE_ALARM", "RESOLVE"): ("409", []),
    ("FALSE_ALARM", "FALSE_ALARM"): ("409", []),
}
TARGET = {"ACKNOWLEDGE": "ACKNOWLEDGED", "RESOLVE": "RESOLVED", "FALSE_ALARM": "FALSE_ALARM"}


def test_the_table_has_all_twelve_cells():
    assert len(CELLS) == 12


@pytest.mark.parametrize("status,action", list(CELLS), ids=[f"{s}-{a}" for s, a in CELLS])
def test_transition(status, action):
    expected = CELLS[(status, action)]
    if isinstance(expected, str):
        assert AlertStateMachine.check(status, action) == expected
        return
    _, allowed = expected
    with pytest.raises(Conflict) as refused:
        AlertStateMachine.check(status, action)
    assert refused.value.status == 409
    assert refused.value.to_body() == {"error": {
        "code": "CONFLICT", "message": "Action not allowed in this state",
        "details": {"rule": "BR-C3-07", "from": status, "to": TARGET[action], "allowed": allowed}}}
