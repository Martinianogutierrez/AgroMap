import time
from unittest.mock import MagicMock

from worker.db import ProgressThrottler


def test_throttle_max_two_per_sec():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    t = ProgressThrottler(min_interval=0.5)
    t.maybe_update(conn, "jid", step="a", progress=1)
    t.maybe_update(conn, "jid", step="b", progress=2)  # dropped
    assert cur.execute.call_count == 1
    time.sleep(0.55)
    t.maybe_update(conn, "jid", step="c", progress=3)
    assert cur.execute.call_count == 2
