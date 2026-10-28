"""P0: lifecycle of Program switches and confirmed on-air time."""

from dj_director.core.switching import AirtimeLedger, SwitchStatus, SwitchTracker


def test_request_lifecycle_and_terminal_states():
    tr = SwitchTracker()
    tr.request(1, "A", "M", 10.0)
    tr.request(2, "B", "A", 11.0)
    tr.request(3, "C", "B", 12.0)
    assert [r.cid for r in tr.pending()] == [1, 2, 3]
    assert tr.confirmed(1, 10.05, 12.0).status == SwitchStatus.CONFIRMED
    assert tr.cancelled(2, 11.5, "superseded by a newer cut").detail.startswith("superseded")
    assert tr.failed(3, 13.0, "scene missing").status == SwitchStatus.FAILED
    # terminal states do not move again
    assert tr.confirmed(2, 12.0) is None and tr.cancelled(1, 12.0, "x") is None
    assert tr.pending() == [] and tr.last_problem().cid == 3


def test_timeout_then_late_confirmation():
    tr = SwitchTracker()
    tr.request(7, "Side", "Master", 100.0)
    tr.failed(7, 103.0, "timed out: SetCurrentProgramScene timed out after 3.0s")
    late = tr.late_confirmation("Side", 103.4)
    assert late is not None and late.status == SwitchStatus.CONFIRMED and "late" in late.detail
    assert tr.late_confirmation("Side", 104.0) is None            # only once


def test_lost_answers_expire():
    tr = SwitchTracker()
    tr.request(1, "A", None, 0.0)
    assert tr.expire(2.0, timeout=5.0) == []
    gone = tr.expire(6.0, timeout=5.0)
    assert [r.cid for r in gone] == [1] and tr.get(1).status == SwitchStatus.FAILED


def test_airtime_counts_only_confirmed_program_time():
    led = AirtimeLedger()
    led.on_program("M", 0.0)
    led.on_program("M", 5.0)              # repeated report: no new segment
    led.on_program("D", 30.0)
    led.on_program("M", 38.0)
    sh = led.shares(now=60.0, window=300)
    assert sh == {"M": 52.0, "D": 8.0}
    assert led.shares(now=60.0, window=25)["M"] == 22.0 and led.shares(now=60.0, window=25)["D"] == 3.0
    assert led.current() == "M"
