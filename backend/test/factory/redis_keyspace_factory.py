import pytest
from faker import Faker

from appointment.defines import REDIS_REMOTE_EVENTS_KEY


class ScanCursorFakeRedis:
    """Mimics real SCAN semantics: each call pulls a fixed-size window, then filters by MATCH. A
    window can match nothing while the cursor is still nonzero, so callers must keep scanning
    until cursor == 0.
    """

    def __init__(self, window=4):
        self.store = {}
        self.window = window
        self._scan_order = None

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed

    def scan(self, cursor, match=None):
        # A fresh walk (cursor == 0) snapshots the current key order and holds it for the whole
        # walk, instead of recomputing list(self.store) on every call. Real callers -- including
        # bust_cached_events itself once a subscriber has more than 500 cached keys -- delete
        # keys mid-scan; recomputing on every call would shift the list underneath a positional
        # cursor and skip keys. Keys deleted since the snapshot was taken are filtered out of
        # each window rather than re-returned.
        if cursor == 0:
            self._scan_order = list(self.store)

        window = [key for key in self._scan_order[cursor : cursor + self.window] if key in self.store]
        cursor += self.window
        if cursor >= len(self._scan_order):
            cursor = 0
        if match is not None:
            prefix = match[:-1] if match.endswith('*') else match
            window = [key for key in window if key.startswith(prefix)]
        return cursor, window

    def scan_iter(self, match=None, count=None):
        cursor = 0
        while True:
            cursor, window = self.scan(cursor, match=match)
            yield from window
            if cursor == 0:
                return


def matching_key(connector, suffix, only_subscriber=True):
    """The Redis key bust_cached_events would target for this connector; only_subscriber mirrors
    its all_calendars argument (True = subscriber-wide, False = also scoped to calendar_id).
    """
    return f'{REDIS_REMOTE_EVENTS_KEY}:{connector.get_key_body(only_subscriber=only_subscriber)}:{suffix}'


@pytest.fixture
def make_redis_keyspace():
    """Builds a populated ScanCursorFakeRedis for a given keyspace shape. Used instead of
    Hypothesis: callers pick layouts by hand with `pytest.mark.parametrize` rather than letting
    `@given(...)` generate and shrink them.
    """
    fake = Faker()

    def _make_redis_keyspace(connector, target_count, unrelated_count, window=4, interleave=False):
        target_keys = [matching_key(connector, i) for i in range(target_count)]
        unrelated_keys = [f'unrelated:{fake.unique.word()}:{i}' for i in range(unrelated_count)]

        if interleave:
            ordered = []
            targets, others = list(target_keys), list(unrelated_keys)
            while targets or others:
                if targets:
                    ordered.append(targets.pop())
                if others:
                    ordered.append(others.pop())
        else:
            ordered = unrelated_keys + target_keys

        redis_instance = ScanCursorFakeRedis(window=window)
        redis_instance.store = dict.fromkeys(ordered, 'x')
        return redis_instance, target_keys, unrelated_keys

    return _make_redis_keyspace
