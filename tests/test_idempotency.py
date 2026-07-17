"""POS-0 — the idempotency claim protocol.

Exercised at the service layer because there is no mutating POS endpoint until
POS-1. The behaviours pinned here are the contract every one of those endpoints
will lean on.
"""
import pytest

from app.core.exceptions import ConflictError
from app.models.pos import IdempotencyKey, IdempotencyStatus
from app.services.idempotency import (
    IdempotencyConflictError,
    IdempotencyService,
    hash_body,
)

ENDPOINT = "pos.orders.create"


def _claim(db, actor, key, body, **kw):
    return IdempotencyService.claim(
        db, actor, endpoint=ENDPOINT, key=key, body=body, **kw
    )


def test_hash_body_is_stable_regardless_of_key_order():
    assert hash_body({"a": 1, "b": 2}) == hash_body({"b": 2, "a": 1})
    assert hash_body({"a": 1}) != hash_body({"a": 2})


def test_first_claim_runs_and_second_replays(db, restaurant_setup):
    actor = restaurant_setup["branch_mgr"]
    body = {"lines": [{"product_id": 1, "quantity": 2}]}

    first = _claim(db, actor, "key-1", body)
    assert first.replay is False
    first.complete({"id": 99, "total": "20.00"}, status=200)
    db.flush()

    # The same key + same body returns the stored result, not a second order.
    second = _claim(db, actor, "key-1", body)
    assert second.replay is True
    assert second.response == {"id": 99, "total": "20.00"}
    assert second.response_status == 200

    # Exactly one key row exists.
    rows = db.query(IdempotencyKey).filter(IdempotencyKey.key == "key-1").all()
    assert len(rows) == 1
    assert rows[0].status == IdempotencyStatus.COMPLETED


def test_same_key_different_body_is_a_client_bug(db, restaurant_setup):
    actor = restaurant_setup["branch_mgr"]
    slot = _claim(db, actor, "key-2", {"qty": 1})
    slot.complete({"ok": True})
    db.flush()

    with pytest.raises(IdempotencyConflictError) as exc:
        _claim(db, actor, "key-2", {"qty": 2})
    assert exc.value.code == "idempotency_key_reuse"
    assert exc.value.status_code == 422


def test_in_flight_duplicate_is_told_to_retry(db, restaurant_setup):
    """A key claimed but not yet completed must not let a second caller through
    to do the work twice."""
    actor = restaurant_setup["branch_mgr"]
    body = {"qty": 1}
    first = _claim(db, actor, "key-3", body)
    assert first.replay is False
    db.flush()

    with pytest.raises(ConflictError) as exc:
        _claim(db, actor, "key-3", body)
    assert exc.value.code == "idempotency_in_flight"


def test_keys_are_scoped_per_endpoint(db, restaurant_setup):
    actor = restaurant_setup["branch_mgr"]
    a = IdempotencyService.claim(
        db, actor, endpoint="pos.orders.create", key="same", body={}
    )
    a.complete({"which": "orders"})
    b = IdempotencyService.claim(
        db, actor, endpoint="pos.payments.create", key="same", body={}
    )
    # Same key on a different endpoint is a different operation, not a replay.
    assert b.replay is False


def test_keys_are_scoped_per_tenant(db, restaurant_setup, make_restaurant, make_user):
    from app.models.enums import UserRole

    actor = restaurant_setup["branch_mgr"]
    slot = _claim(db, actor, "shared-key", {"qty": 1})
    slot.complete({"id": 1})
    db.flush()

    other_r = make_restaurant("Other")
    other_actor = make_user(
        "other-mgr@test.com", UserRole.BRANCH_MANAGER, restaurant_id=other_r.id
    )
    # Another restaurant reusing the same key string must not see our response.
    theirs = _claim(db, other_actor, "shared-key", {"qty": 1})
    assert theirs.replay is False


def test_completing_a_replay_slot_is_a_no_op(db, restaurant_setup):
    actor = restaurant_setup["branch_mgr"]
    first = _claim(db, actor, "key-4", {"qty": 1})
    first.complete({"id": 1}, status=200)
    db.flush()

    replay = _claim(db, actor, "key-4", {"qty": 1})
    assert replay.replay is True
    replay.complete({"id": 2}, status=500)  # must not overwrite the real result
    db.flush()

    row = db.query(IdempotencyKey).filter(IdempotencyKey.key == "key-4").one()
    assert row.response_body == {"id": 1}
    assert row.response_status == 200


def test_rolled_back_work_takes_its_key_with_it(db, restaurant_setup):
    """The point of claiming inside the caller's transaction: a failed request
    must not leave a key claimed for work that never happened."""
    actor = restaurant_setup["branch_mgr"]
    db.commit()  # baseline the outer savepoint

    slot = _claim(db, actor, "key-5", {"qty": 1})
    assert slot.replay is False
    db.rollback()

    # The key is gone, so the client gets a clean retry rather than a phantom
    # replay of a request that never succeeded.
    assert db.query(IdempotencyKey).filter(IdempotencyKey.key == "key-5").count() == 0
    retry = _claim(db, actor, "key-5", {"qty": 1})
    assert retry.replay is False
