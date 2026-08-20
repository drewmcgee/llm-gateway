import asyncio

DEFAULT_MAXSIZE = 1000


class Broadcaster:
    def __init__(self, maxsize=DEFAULT_MAXSIZE):
        self._subscribers = set()
        self._maxsize = maxsize

    def subscribe(self):
        queue = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)

    def publish(self, event):
        for queue in self._subscribers:
            if queue.full():
                # A slow/stalled consumer under load shouldn't grow this queue
                # without bound (OOM risk) or block the publisher -- drop the
                # oldest buffered event so the live tail stays current.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
