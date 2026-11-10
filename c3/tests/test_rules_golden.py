"""Golden test of GET /alert-rules: C3 answers exactly like the stub (stubs/c3.py) for every role.
The seed has no tenant overrides, so the effective list is the 7 platform defaults for everyone;
the comparison (status, JSON) is the one of test_alerts_golden.py, with no normalizer needed."""
from __future__ import annotations

import pytest

from test_alerts_golden import compare, stub_client  # noqa: F401  (the stub fixture and the comparison)

REQUESTS = [
    ("maria", {}), ("maria", {"tenantId": 1}),               # a fleet manager's tenant comes from the token
    ("staff", {}), ("staff", {"tenantId": 2}), ("staff", {"tenantId": "tB"}), ("staff", {"tenantId": "tA"}),
    ("admin", {}), ("admin", {"tenantId": 1}),
    ("service", {}), ("service", {"tenantId": "tB"}),
    ("staff", {"tenantId": "tZ"}),                            # 400 unknown tenant code
    ("anh", {}), ("rider", {}), ("iot", {}), ("iot", {"tenantId": 1}),  # 403
    (None, {}),                                               # 401
]


@pytest.mark.parametrize("who,params", REQUESTS, ids=[f"{w}-{p}" for w, p in REQUESTS])
async def test_rules_match_stub(stub_client, client, who, params):
    status, body = await compare(stub_client, client, who, "/alert-rules", params)
    if status == 200:
        assert [r["ruleId"] for r in body] == [1, 2, 3, 4, 5, 6, 7] and {r["scope"] for r in body} == {"DEFAULT"}


async def test_field_order_is_the_stubs(stub_client, client):
    from conftest import bearer, token_of
    ours = (await client.get("/api/v1/alert-rules", headers=bearer(token_of("maria")))).json()
    stub = (await stub_client.get("/api/v1/alert-rules", headers=bearer(token_of("maria")))).json()
    assert [list(r) for r in ours] == [list(r) for r in stub]
