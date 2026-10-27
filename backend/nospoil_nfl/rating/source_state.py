"""Leased reconciliation control items, deliberately absent from the game GSI."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
from uuid import uuid4

from botocore.exceptions import ClientError


@dataclass
class SourceState:
    next_probe: str | None = None
    cooldown_until: str | None = None
    etag: str | None = None
    modified: str | None = None
    revision: str | None = None
    schedule_revision: str | None = None
    fallback_after: str | None = None
    failures: int = 0
    evaluated: dict[str, str] = field(default_factory=dict)
    corrected_cycle: str | None = None
    corrected: dict[str, str] = field(default_factory=dict)


class DynamoSourceStateRepository:
    """Uses only existing GetItem/UpdateItem permissions, with expiring leases."""

    def __init__(self, table) -> None:
        self._table = table

    @staticmethod
    def _key(season: int) -> dict[str, str]:
        # No season_key or schedule_key: DynamoDB never projects this into the
        # sparse season-schedule-index, where readers expect strict Game items.
        return {"game_id": f"NFLVERSE_SOURCE#{season}"}

    def load(self, season: int) -> SourceState:
        item = self._table.get_item(Key=self._key(season), ConsistentRead=True).get(
            "Item", {}
        )
        return SourceState(**json.loads(item.get("source_state", "{}")))

    def acquire(self, season: int, now: datetime) -> str | None:
        token = uuid4().hex
        try:
            self._table.update_item(
                Key=self._key(season),
                UpdateExpression="SET lease_token = :token, lease_until = :until",
                ConditionExpression="attribute_not_exists(lease_until) OR lease_until <= :now",
                ExpressionAttributeValues={
                    ":token": token,
                    ":until": int((now + timedelta(minutes=25)).timestamp()),
                    ":now": int(now.timestamp()),
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return token

    def save_and_release(self, season: int, token: str, state: SourceState) -> None:
        self._table.update_item(
            Key=self._key(season),
            UpdateExpression="SET source_state = :state REMOVE lease_token, lease_until",
            ConditionExpression="lease_token = :token",
            ExpressionAttributeValues={
                ":token": token,
                ":state": json.dumps(
                    asdict(state), sort_keys=True, separators=(",", ":")
                ),
            },
        )
