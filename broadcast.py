import asyncio


class Broadcaster:
    def __init__(self):
        self._subscribers = set()

    def subscribe(self):
        queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)

    def publish(self, event):
        for queue in self._subscribers:
            queue.put_nowait(event)
