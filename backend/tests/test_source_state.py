"""Validate the control-item contract independently of strict game decoding."""

from datetime import UTC, datetime
import json

from botocore.exceptions import ClientError
import pytest

from backend.nospoil_nfl.rating.source_state import (
    DynamoSourceStateRepository,
    SourceState,
)


class Table:
    def __init__(self):
        self.items = {}
        self.now = 0

    def get_item(self, *, Key, ConsistentRead):
        assert ConsistentRead
        return {"Item": self.items.get(Key["game_id"], {})}

    def update_item(
        self, *, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues
    ):
        item = self.items.setdefault(Key["game_id"], {})
        values = ExpressionAttributeValues
        if ":until" in values:
            if item.get("lease_until", 0) > values[":now"]:
                self.conflict()
            item.update(lease_token=values[":token"], lease_until=values[":until"])
        else:
            if item.get("lease_token") != values[":token"]:
                self.conflict()
            item["source_state"] = values[":state"]
            item.pop("lease_token", None)
            item.pop("lease_until", None)

    @staticmethod
    def conflict():
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
        )


def test_control_items_are_environment_table_scoped_and_absent_from_game_index():
    table = Table()
    repository = DynamoSourceStateRepository(table)
    now = datetime(2026, 10, 27, tzinfo=UTC)
    assert repository.load(2026) == SourceState()
    token = repository.acquire(2026, now)
    state = SourceState(revision="v1", evaluated={"due:123": "hash"})
    repository.save_and_release(2026, token, state)
    assert repository.load(2026) == state
    assert repository.load(2025) == SourceState()
    item = table.items["NFLVERSE_SOURCE#2026"]
    assert "season_key" not in item and "schedule_key" not in item
    assert "lease_token" not in item


def test_expired_lease_recovers_but_old_worker_cannot_commit_over_new_owner():
    table = Table()
    repository = DynamoSourceStateRepository(table)
    now = datetime(2026, 10, 27, tzinfo=UTC)
    first = repository.acquire(2026, now)
    assert repository.acquire(2026, now) is None
    second = repository.acquire(2026, now.replace(hour=1))
    assert second and second != first
    with pytest.raises(ClientError):
        repository.save_and_release(2026, first, SourceState(revision="old"))
    repository.save_and_release(2026, second, SourceState(revision="new"))
    assert repository.load(2026).revision == "new"


def test_invalid_state_is_not_silently_reset():
    table = Table()
    table.items["NFLVERSE_SOURCE#2026"] = {"source_state": "broken"}
    with pytest.raises(json.JSONDecodeError):
        DynamoSourceStateRepository(table).load(2026)
