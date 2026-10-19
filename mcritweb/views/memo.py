import threading
from collections import OrderedDict


class BoundedMemo:
    """A process-local, least-recently-used store for values that are pure functions of
    their key, safe to share between the threads of one server process.

    Bounded by a total weight: every entry weighs `weigh(value)`, 1 unless told
    otherwise, so the bound is an entry count by default and can be an estimate of
    bytes where entries differ a lot in size. Once over the bound, the entries used
    least recently are evicted, so a caller choosing keys (a query parameter, say) can
    churn it but not grow it. A value heavier than the whole bound is not kept at all.
    Values are handed out shared, not copied, so everyone who receives one must treat
    it as read-only.
    """

    def __init__(self, max_weight, weigh=None):
        self._max_weight = max_weight
        self._weigh = weigh or (lambda value: 1)
        self._weight = 0
        # key -> (value, weight)
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    def lookup(self, key, default=None):
        """The value stored under `key`, or `default` if there is none."""
        with self._lock:
            if key not in self._entries:
                return default
            self._entries.move_to_end(key)
            return self._entries[key][0]

    def store(self, key, value):
        weight = self._weigh(value)
        with self._lock:
            if key in self._entries:
                self._weight -= self._entries.pop(key)[1]
            if weight > self._max_weight:
                return
            self._entries[key] = (value, weight)
            self._weight += weight
            while self._weight > self._max_weight:
                _, (_, evicted_weight) = self._entries.popitem(last=False)
                self._weight -= evicted_weight

    def get(self, key, compute):
        """The value stored under `key`, computing and storing it on a miss."""
        missing = object()
        value = self.lookup(key, missing)
        if value is missing:
            # computed without holding the lock, so a slow miss does not stall every
            # other request. Two concurrent misses on one key both compute it; since the
            # value is a pure function of the key, either result is as good as the other.
            value = compute()
            self.store(key, value)
        return value

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._weight = 0

    def __len__(self):
        with self._lock:
            return len(self._entries)


#: The key in app.extensions under which the named memos of an app are kept, so that
#: every app - every test app included - has its own.
MEMOS_EXTENSION = "mcritweb.memos"


def app_memo(app, name, max_weight, weigh=None):
    """The memo `name` of `app`, created with the given bound on first use."""
    memos = app.extensions.setdefault(MEMOS_EXTENSION, {})
    memo = memos.get(name)
    if memo is None:
        memo = memos.setdefault(name, BoundedMemo(max_weight, weigh=weigh))
    return memo


def clear_app_memos(app):
    """Forget everything memoized for `app`, e.g. after the backend was reset."""
    for memo in list(app.extensions.get(MEMOS_EXTENSION, {}).values()):
        memo.clear()
