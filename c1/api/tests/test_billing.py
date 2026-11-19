"""Billing (report section 5.1): the invoice formula, the price list (SYS_ADMIN edits, audited) and
who may see which invoice."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.errors import Validation
from app.services.billing_service import DEFAULT_PRICES, compute_invoice, month_range


def test_invoice_formula_fleet_over_every_allowance():
    usage = {"vehicles": 12, "clips": 150_500, "emails": 5_250, "storageGb": 2.5}
    bill = compute_invoice(DEFAULT_PRICES["FLEET"], usage)
    amounts = {line["code"]: line["amountCents"] for line in bill["lines"]}
    # 2 extra vehicles x $12; 51 blocks of 1,000 clips x $0.40; 3 blocks of 100 emails x $0.80; 2.5 GB x $0.08
    assert amounts == {"PLAN": 19_900, "VEHICLES": 2_400, "CLIPS": 2_040, "EMAILS": 240, "STORAGE": 20}
    assert bill["totalCents"] == 24_600 == bill["projectedTotalCents"]


def test_invoice_within_the_allowances_is_the_plan_fee_and_projection_scales_usage():
    usage = {"vehicles": 1, "clips": 6_000, "emails": 10, "storageGb": 0.0}
    bill = compute_invoice(DEFAULT_PRICES["BASIC"], usage, fraction=0.5)
    assert bill["totalCents"] == 999                      # 6,000 clips are inside the 10,000 included
    assert bill["projectedTotalCents"] == 999 + 2 * 50    # 12,000 projected: 2 blocks of 1,000 over


def test_month_range():
    now = datetime(2026, 11, 19, 12, 0)
    label, start, nxt, end, running = month_range(None, now)
    assert (label, start, nxt, end, running) == ("2026-11", datetime(2026, 11, 1), datetime(2026, 12, 1), now, True)
    label, start, nxt, end, running = month_range("2026-10", now)
    assert running is False and end == nxt == datetime(2026, 11, 1)
    assert month_range("2026-12", datetime(2026, 12, 5))[2] == datetime(2027, 1, 1)
    for bad in ("2026-13", "nov", "2027-01"):
        with pytest.raises(Validation):
            month_range(bad, now)


async def test_price_list_defaults_and_admin_update_is_audited(client, auth):
    owner = await auth.headers("anh")
    prices = (await client.get("/api/v1/billing/prices", headers=owner)).json()
    assert [p["plan"] for p in prices] == ["BASIC", "FLEET", "ENTERPRISE"]
    assert prices[0]["monthlyFeeCents"] == 999 and prices[1]["includedVehicles"] == 10
    assert (await client.put("/api/v1/billing/prices/BASIC", headers=owner, json={"monthlyFeeCents": 1})).status_code == 403
    admin = await auth.headers("admin")
    changed = await client.put("/api/v1/billing/prices/BASIC", headers=admin, json={"monthlyFeeCents": 1099})
    assert changed.status_code == 200 and changed.json()["monthlyFeeCents"] == 1099
    audit = (await client.get("/api/v1/admin/audit", headers=admin, params={"action": "PRICE_UPDATE"})).json()
    assert audit["items"][0]["before"]["monthly_fee_cents"] == 999 and audit["items"][0]["after"]["monthly_fee_cents"] == 1099
    assert (await client.put("/api/v1/billing/prices/GOLD", headers=admin, json={"monthlyFeeCents": 1})).status_code == 404
    assert (await client.put("/api/v1/billing/prices/BASIC", headers=admin, json={"monthlyFeeCents": -5})).status_code == 400
    back = await client.put("/api/v1/billing/prices/BASIC", headers=admin, json={"monthlyFeeCents": 999})
    assert back.json()["monthlyFeeCents"] == 999


async def test_owner_sees_the_own_invoice_with_measured_usage(client, auth):
    owner = await auth.headers("anh")
    inv = (await client.get("/api/v1/billing/invoice", headers=owner, params={"tenantId": 2})).json()
    assert inv["tenantId"] == 1 and inv["plan"] == "BASIC" and inv["currency"] == "USD"   # tenantId is ignored
    assert inv["usage"]["vehicles"] == 1 and inv["usage"]["clips"] >= 0 and inv["running"] is True
    assert [line["code"] for line in inv["lines"]] == ["PLAN", "VEHICLES", "CLIPS", "EMAILS", "STORAGE"]
    assert inv["totalCents"] == sum(line["amountCents"] for line in inv["lines"])
    assert set(inv["usage"]["notMeasured"]) == {"emails", "storage"}   # no C2/C3 tables in the C1 tests


async def test_staff_pick_the_tenant_and_see_the_summary(client, auth):
    staff = await auth.headers("staff")
    assert (await client.get("/api/v1/billing/invoice", headers=staff)).status_code == 400
    fleet = (await client.get("/api/v1/billing/invoice", headers=staff, params={"tenantId": 2})).json()
    assert fleet["plan"] == "FLEET" and fleet["usage"]["vehicles"] == 5
    summary = (await client.get("/api/v1/billing/summary", headers=staff, params={"month": "2026-10"})).json()
    assert summary["month"] == "2026-10" and summary["running"] is False and len(summary["tenants"]) >= 2
    assert summary["totalCents"] >= 999 + 19_900


async def test_billing_roles(client, auth):
    for who in ("rider", "iot"):
        headers = await auth.headers(who)
        assert (await client.get("/api/v1/billing/invoice", headers=headers, params={"tenantId": 1})).status_code == 403
    owner = await auth.headers("anh")
    assert (await client.get("/api/v1/billing/summary", headers=owner)).status_code == 403
    me = (await client.get("/api/v1/auth/me", headers=owner)).json()
    assert "billing" in me["permissions"]
