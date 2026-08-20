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
