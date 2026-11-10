import asyncio

from c4_worker.prio_queue import PriorityEventQueue, QueuedEvent


def ev(n: int, priority: str = "NORMAL") -> QueuedEvent:
    return QueuedEvent.build("tA", "AV-01", {"eventId": f"e{n}", "edge": {"priority": priority}}, mid=n)


def drain(q: PriorityEventQueue):
    out = []
    while (item := q.get_nowait()) is not None:
        out.append(item.payload["eventId"])
    return out


def test_high_first_fifo_within_level():
    q = PriorityEventQueue(10)
    for item in [ev(1), ev(2, "HIGH"), ev(3), ev(4, "HIGH"), ev(5, "high")]:
        assert q.put(item) is None
    assert drain(q) == ["e2", "e4", "e5", "e1", "e3"]


def test_missing_or_unknown_priority_is_normal():
    assert not QueuedEvent.build("tA", "AV-01", {"eventId": "x"}).high
    assert not QueuedEvent.build("tA", "AV-01", {"eventId": "x", "edge": "bad"}).high


def test_full_drops_oldest_normal_and_counts():
    q = PriorityEventQueue(3)
    q.put(ev(1))
    q.put(ev(2, "HIGH"))
    q.put(ev(3))
    dropped = q.put(ev(4, "HIGH"))
    assert dropped.payload["eventId"] == "e1" and q.dropped == 1 and len(q) == 3
    dropped = q.put(ev(5))                     # a NORMAL arrival also pushes out the oldest NORMAL
    assert dropped.payload["eventId"] == "e3" and q.dropped == 2
    assert drain(q) == ["e2", "e4", "e5"]


def test_full_of_high_drops_the_newcomer():
    q = PriorityEventQueue(2)
    q.put(ev(1, "HIGH"))
    q.put(ev(2, "HIGH"))
    new = ev(3)
    assert q.put(new) is new
    new_high = ev(4, "HIGH")
    assert q.put(new_high) is new_high
    assert q.dropped == 2 and drain(q) == ["e1", "e2"]


def test_bounded_at_200():
    q = PriorityEventQueue()
    for i in range(250):
        q.put(ev(i, "HIGH" if i % 10 == 0 else "NORMAL"))
    assert len(q) == 200 and q.dropped == 50
    out = drain(q)
    assert out[:25] == [f"e{i}" for i in range(0, 250, 10)]   # every HIGH kept, first
    assert out[25:] == [f"e{i}" for i in range(250) if i % 10][-175:]   # the newest NORMAL ones


async def test_get_waits_for_an_item():
    q = PriorityEventQueue(5)
    task = asyncio.create_task(q.get())
    await asyncio.sleep(0.01)
    assert not task.done()
    q.put(ev(7))
    assert (await asyncio.wait_for(task, 1)).payload["eventId"] == "e7"
