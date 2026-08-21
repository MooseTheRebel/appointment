"""Tests for BaseConnector.bust_cached_events(): a bust must cover the full keyspace, not just
the first SCAN window.

Redis's SCAN can return zero matches on a call even though more matching keys exist later in the
keyspace -- it only guarantees completion once the cursor returns to 0. The pre-fix code stopped
after a single scan() call, so it silently missed keys sitting outside that first window.

ScanCursorFakeRedis (test/factory/redis_keyspace_factory.py) simulates this multi-window scan
behavior, and make_redis_keyspace generates varied keyspace layouts to test it. See that fixture's
docstring for details.
"""

import pytest

from appointment.controller.calendar import BaseConnector
from appointment.defines import REDIS_REMOTE_EVENTS_KEY
from factory.redis_keyspace_factory import ScanCursorFakeRedis


class TestBustCachedEvents:
    def test_deletes_every_key_even_when_an_early_window_matches_nothing(self, make_redis_keyspace):
        """Target keys sit behind unrelated ones, so the first SCAN window matches nothing even
        though the cursor is nonzero. The bust must keep scanning anyway.
        """
        connector = BaseConnector(subscriber_id=1, calendar_id=None, redis_instance=None)
        redis_instance, target_keys, unrelated_keys = make_redis_keyspace(
            connector, target_count=6, unrelated_count=20, window=4
        )
        connector.redis_instance = redis_instance

        # Precondition: the first window really is empty, with more keyspace left to walk.
        cursor, first_window = redis_instance.scan(0, match=f'{REDIS_REMOTE_EVENTS_KEY}:*')
        assert first_window == []
        assert cursor != 0

        deleted = connector.bust_cached_events(all_calendars=True)

        assert deleted == 6
        assert set(redis_instance.store) == set(unrelated_keys)

    def test_leaves_other_prefixes_alone(self, make_redis_keyspace):
        connector = BaseConnector(subscriber_id=1, calendar_id=None, redis_instance=None)
        redis_instance, target_keys, unrelated_keys = make_redis_keyspace(
            connector, target_count=10, unrelated_count=1, window=3
        )
        connector.redis_instance = redis_instance

        deleted = connector.bust_cached_events(all_calendars=True)

        assert deleted == 10
        assert set(redis_instance.store) == set(unrelated_keys)

    def test_returns_zero_when_nothing_matches(self):
        connector = BaseConnector(subscriber_id=1, calendar_id=None, redis_instance=ScanCursorFakeRedis())
        assert connector.bust_cached_events(all_calendars=True) == 0

    def test_is_a_noop_without_redis(self):
        connector = BaseConnector(subscriber_id=1, calendar_id=None, redis_instance=None)
        assert connector.bust_cached_events(all_calendars=True) == 0

    @pytest.mark.parametrize(
        'target_count, unrelated_count, window, interleave',
        [
            (0, 0, 4, False),  # nothing cached at all
            (0, 12, 3, False),  # only unrelated keys -- nothing should be deleted
            (12, 0, 3, False),  # only target keys, no noise to skip over
            (6, 20, 4, False),  # early window matches nothing (the pre-fix failure case)
            (1, 1, 1, True),  # smallest possible interleaved case
            (30, 30, 2, True),  # tiny window, heavy interleave -- many multi-call walks
            (25, 3, 5, False),  # target-heavy, unrelated keys squeezed into few windows
        ],
    )
    def test_deletes_exactly_the_matching_keys_regardless_of_layout(
        self, make_redis_keyspace, target_count, unrelated_count, window, interleave
    ):
        """No matter how the keys are laid out across the keyspace, a bust must delete exactly
        the matching keys and nothing else.
        """
        connector = BaseConnector(subscriber_id=1, calendar_id=None, redis_instance=None)
        redis_instance, target_keys, unrelated_keys = make_redis_keyspace(
            connector, target_count, unrelated_count, window=window, interleave=interleave
        )
        connector.redis_instance = redis_instance

        deleted = connector.bust_cached_events(all_calendars=True)

        assert deleted == len(target_keys)
        assert set(redis_instance.store) == set(unrelated_keys)
