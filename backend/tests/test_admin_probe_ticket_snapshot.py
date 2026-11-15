import pytest
from scripts.admin_probe import check_ops_tickets


def response(ids):
    return {"loop_ready": True, "groups": [{"tickets": [
        {"id": i, "status": "open"} for i in ids]}]}


def test_concurrent_ticket_insert_does_not_invalidate_live_response():
    truth = {"tickets": 1, "ticket_ids_before": {1, 2}, "ticket_ids_after": {1, 2, 3}}
    assert check_ops_tickets(response([1, 2, 3]), truth)
    assert check_ops_tickets(response([1, 2]), truth)


@pytest.mark.parametrize("ids", [[1, 3], [1, 2, 4], [1, 2, 2]])
def test_missing_unknown_and_duplicate_tickets_still_fail(ids):
    truth = {"ticket_ids_before": {1, 2}, "ticket_ids_after": {1, 2, 3}}
    with pytest.raises(AssertionError):
        check_ops_tickets(response(ids), truth)


def test_failed_snapshot_does_not_pass_a_count():
    with pytest.raises(AssertionError):
        check_ops_tickets(response([1]), {"ticket_ids_before": {1}, "ticket_snapshot_error": True})
