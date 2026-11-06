"""Table 104 (recipients and channels) as pure unit tests: one per column N1 to N4, quiet hours
across midnight in America/Los_Angeles (both sides of daylight saving time), and the rules on
who is reachable."""
from __future__ import annotations

from datetime import datetime, time

import pytest

from c3_service.services.recipient_resolver import (Channels, Person, Recipient, channels_for, in_quiet_hours,
                                                    local_time_of, resolve)

LA = "America/Los_Angeles"
NIGHT = (time(22, 0), time(6, 0))
STAFF = [Person(1001, "anh@example.com")]
RIDERS = [Person(1003, "rider@example.com")]
DASHBOARD = Recipient("DASHBOARD", None, None)
ANH = Recipient("EMAIL", 1001, "anh@example.com")
RIDER = Recipient("EMAIL", 1003, "rider@example.com")
NOON_LA = datetime(2026, 11, 6, 20, 0)      # 12:00 PST (UTC-8)
NIGHT_LA = datetime(2026, 11, 7, 7, 10)     # 23:10 PST: the SC2 time


# -- the columns ------------------------------------------------------------------------------------
def test_N1_no_email_channel_dashboard_only():
    assert channels_for(email_selected=False, quiet=False, riders_active=True) == Channels(True, False, False)
    assert resolve("DASHBOARD", None, None, True, NOON_LA, LA, STAFF, RIDERS) == [DASHBOARD]


def test_N2_inside_quiet_hours_dashboard_only():
    assert channels_for(email_selected=True, quiet=True, riders_active=True) == Channels(True, False, False)
    assert resolve("DASHBOARD,EMAIL", *NIGHT, True, NIGHT_LA, LA, STAFF, RIDERS) == [DASHBOARD]


def test_N3_email_to_owner_and_fleet_managers_no_active_rider():
    assert channels_for(email_selected=True, quiet=False, riders_active=False) == Channels(True, True, False)
    assert resolve("DASHBOARD,EMAIL", *NIGHT, True, NOON_LA, LA, STAFF, []) == [DASHBOARD, ANH]
    # notify_riders off: riders are not emailed even when one is active
    assert resolve("DASHBOARD,EMAIL", *NIGHT, False, NOON_LA, LA, STAFF, RIDERS) == [DASHBOARD, ANH]


def test_N4_email_to_staff_and_the_active_riders():
    assert channels_for(email_selected=True, quiet=False, riders_active=True) == Channels(True, True, True)
    assert resolve("DASHBOARD,EMAIL", *NIGHT, True, NOON_LA, LA, STAFF, RIDERS) == [DASHBOARD, ANH, RIDER]


# -- quiet hours ------------------------------------------------------------------------------------
@pytest.mark.parametrize("local,inside", [
    (time(21, 59), False), (time(22, 0), True), (time(23, 10), True), (time(0, 0), True),
    (time(5, 59), True), (time(6, 0), False), (time(12, 0), False)])
def test_quiet_hours_across_midnight(local, inside):
    assert in_quiet_hours(local, *NIGHT) is inside


@pytest.mark.parametrize("local,inside", [(time(8, 59), False), (time(9, 0), True), (time(16, 59), True),
                                          (time(17, 0), False)])
def test_quiet_hours_within_one_day(local, inside):
    assert in_quiet_hours(local, time(9, 0), time(17, 0)) is inside


@pytest.mark.parametrize("quiet", [(None, None), (time(22, 0), None), (None, time(6, 0)), (time(8, 0), time(8, 0))])
def test_no_quiet_hours_without_both_ends(quiet):
    assert in_quiet_hours(time(23, 0), *quiet) is False


@pytest.mark.parametrize("utc,local_hhmm,inside", [
    (datetime(2026, 11, 7, 7, 10), "23:10", True),    # winter, PST = UTC-8 (SC2)
    (datetime(2026, 12, 1, 5, 30), "21:30", False),   # winter: 05:30 UTC is still before 22:00 in LA
    (datetime(2026, 7, 1, 5, 30), "22:30", True),     # summer, PDT = UTC-7: the same UTC time is inside
    (datetime(2026, 7, 1, 13, 30), "06:30", False)])
def test_quiet_hours_are_evaluated_in_the_tenants_time_zone(utc, local_hhmm, inside):
    local = local_time_of(utc, LA)
    assert local.strftime("%H:%M") == local_hhmm
    assert in_quiet_hours(local.time(), *NIGHT) is inside


def test_unknown_time_zone_falls_back_to_utc():
    assert local_time_of(datetime(2026, 11, 7, 7, 10), "Mars/Olympus").strftime("%H:%M") == "07:10"


# -- who is reachable -------------------------------------------------------------------------------
def test_inactive_users_and_users_without_email_get_no_email():
    staff = [Person(1001, "anh@example.com"), Person(1009, "gone@example.com", active=False), Person(1010, None)]
    assert resolve("EMAIL", None, None, False, NOON_LA, LA, staff, []) == [DASHBOARD, ANH]


def test_a_user_who_is_both_staff_and_rider_gets_one_email():
    assert resolve("EMAIL", None, None, True, NOON_LA, LA, STAFF, [Person(1001, "anh@example.com")]) == [DASHBOARD, ANH]


def test_unreachable_riders_count_as_no_active_rider():
    assert channels_for(True, False, riders_active=False).email_riders is False
    assert resolve("EMAIL", None, None, True, NOON_LA, LA, STAFF, [Person(1003, None)]) == [DASHBOARD, ANH]


def test_sms_and_push_are_design_only():
    assert resolve("DASHBOARD,SMS,PUSH", None, None, True, NOON_LA, LA, STAFF, RIDERS) == [DASHBOARD]
