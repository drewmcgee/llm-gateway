import pytest

from broadcast import Broadcaster
from main import format_sse_event


def test_subscribe_returns_independent_queues():
    broadcaster = Broadcaster()
    q1 = broadcaster.subscribe()
    q2 = broadcaster.subscribe()

    broadcaster.publish({"id": 1})

    assert q1.get_nowait() == {"id": 1}
    assert q2.get_nowait() == {"id": 1}


def test_unsubscribed_queue_receives_nothing_further():
    broadcaster = Broadcaster()
    queue = broadcaster.subscribe()
    broadcaster.unsubscribe(queue)

    broadcaster.publish({"id": 1})

    assert queue.empty()


def test_publish_with_no_subscribers_does_not_raise():
    broadcaster = Broadcaster()
    broadcaster.publish({"id": 1})  # should not raise


@pytest.mark.asyncio
async def test_subscriber_receives_events_in_order():
    broadcaster = Broadcaster()
    queue = broadcaster.subscribe()

    broadcaster.publish({"id": 1})
    broadcaster.publish({"id": 2})

    assert await queue.get() == {"id": 1}
    assert await queue.get() == {"id": 2}


def test_format_sse_event_matches_sse_wire_format():
    assert format_sse_event({"id": 1}) == 'data: {"id": 1}\n\n'


def test_queue_never_exceeds_maxsize_under_burst():
    broadcaster = Broadcaster(maxsize=10)
    queue = broadcaster.subscribe()

    for i in range(1000):
        broadcaster.publish({"id": i})

    assert queue.qsize() == 10


def test_full_queue_drops_oldest_to_admit_newest():
    broadcaster = Broadcaster(maxsize=2)
    queue = broadcaster.subscribe()

    broadcaster.publish({"id": 1})
    broadcaster.publish({"id": 2})
    broadcaster.publish({"id": 3})  # queue is full here -- id 1 should drop

    assert queue.qsize() == 2
    assert queue.get_nowait() == {"id": 2}
    assert queue.get_nowait() == {"id": 3}


def test_slow_subscriber_does_not_block_other_subscribers():
    broadcaster = Broadcaster(maxsize=1)
    slow = broadcaster.subscribe()  # never drained, simulates a stalled client
    fast = broadcaster.subscribe()

    for i in range(50):
        broadcaster.publish({"id": i})
        fast.get_nowait()  # drained immediately, simulates a healthy client

    assert slow.qsize() == 1  # bounded, not 50
    assert slow.get_nowait() == {"id": 49}  # still holds the most recent event
