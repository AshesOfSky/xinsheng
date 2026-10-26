"""A rolling group-chat inbox: keep mentions, coalesce ordinary chatter."""
import threading
import time


class ConversationInbox:
    def __init__(self):
        self.condition = threading.Condition()
        self.pending = {}
        self.latest_id = 0
        self.changed_at = 0.0
        self.first_pending_at = 0.0

    @staticmethod
    def required(message):
        return bool(message['is_at'] or message.get('force_reply'))

    def put(self, message):
        with self.condition:
            now = time.monotonic()
            if not self.pending:
                self.first_pending_at = now
            self.pending[message['id']] = message
            self.latest_id = max(self.latest_id, message['id'])
            self.changed_at = now
            self.condition.notify_all()

    def take(self, stop, not_before=0.0, timeout=None):
        deadline = time.monotonic()+timeout if timeout is not None else None
        with self.condition:
            while not stop.is_set():
                if self.pending:
                    urgent = any(self.required(m) for m in self.pending.values())
                    # At most 1.2 s to collect a burst; @ bypasses the wait.
                    ready_at = max(not_before, min(self.changed_at + 0.55,
                                                   self.first_pending_at + 1.2))
                    if urgent or time.monotonic() >= ready_at:
                        batch = sorted(self.pending.values(), key=lambda m: m['id'])
                        self.pending.clear()
                        return batch
                elif deadline is not None and time.monotonic()>=deadline:
                    return []
                self.condition.wait(0.1)
        return []

    def restore_required(self, batch):
        with self.condition:
            for message in batch:
                if self.required(message):
                    self.pending[message['id']] = message
            self.condition.notify_all()

    def has_newer(self, message_id):
        with self.condition:
            return self.latest_id > message_id

    def has_mention(self):
        with self.condition:
            return any(m['is_at'] for m in self.pending.values())

    def size(self):
        with self.condition:
            return len(self.pending)
