import threading
import time

import pytest

from app.services.graph_execution_pool import (
    GraphExecutionPool,
    GraphExecutionRejected,
    reset_graph_execution_pool,
)


@pytest.fixture(autouse=True)
def _reset_pool(test_settings):
    reset_graph_execution_pool()
    yield
    reset_graph_execution_pool()


def test_pool_allows_up_to_max_concurrent(test_settings):
    pool = GraphExecutionPool(max_concurrent=2, acquire_timeout_sec=2.0)
    entered = []

    def hold():
        with pool.acquire():
            entered.append(1)
            time.sleep(0.15)

    t1 = threading.Thread(target=hold)
    t2 = threading.Thread(target=hold)
    t1.start()
    t2.start()
    time.sleep(0.05)
    assert pool.stats()["active"] == 2
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert len(entered) == 2


def test_pool_rejects_when_saturated():
    pool = GraphExecutionPool(max_concurrent=1, acquire_timeout_sec=0.1)

    with pool.acquire():
        with pytest.raises(GraphExecutionRejected):
            with pool.acquire():
                pass
