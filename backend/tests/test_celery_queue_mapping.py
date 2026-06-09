from app.services.celery_queue_inspector import (
    FIVE_VERST_BATCH_QUEUE,
    FIVE_VERST_USER_QUEUE,
    S95_BATCH_QUEUE,
    S95_USER_QUEUE,
    TASK_QUEUE_BY_SUFFIX,
)


def test_user_sync_queue_mapping() -> None:
    assert TASK_QUEUE_BY_SUFFIX["five_verst"] == FIVE_VERST_USER_QUEUE
    assert TASK_QUEUE_BY_SUFFIX["s95"] == S95_USER_QUEUE
    assert FIVE_VERST_USER_QUEUE == "five_verst_user"
    assert FIVE_VERST_BATCH_QUEUE == "five_verst"
    assert S95_USER_QUEUE == "s95_user"
    assert S95_BATCH_QUEUE == "s95"
