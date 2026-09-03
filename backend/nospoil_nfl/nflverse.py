"""Season-scoped nflverse ingestion and ESPN game ID mapping."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .game.models import DomainValidationError, GameId
from .providers.contracts import NFLVersePlayProvider, NFLVerseScheduleProvider
from .providers.models import (
    NflversePlay,
    NflversePlaySeason,
    NflverseScheduleGame,
    NflverseScheduleSeason,
)


class NflverseGameMappingError(LookupError):
    """Raised when a season schedule has no mapping for an ESPN event ID."""

    def __init__(self, *, espn_id: GameId, season: int) -> None:
        self.espn_id = espn_id
        self.season = season
        super().__init__(
            f"no nflverse schedule mapping for ESPN game {espn_id} "
            f"in season {season}"
        )


@dataclass(frozen=True, slots=True)
class MappedNflverseGame:
    """A verified schedule mapping and its locally selected play observations."""

    schedule_game: NflverseScheduleGame
    plays: tuple[NflversePlay, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.schedule_game, NflverseScheduleGame):
            raise DomainValidationError(
                "schedule_game must be an NflverseScheduleGame"
            )
        if not isinstance(self.plays, tuple) or any(
            not isinstance(play, NflversePlay) for play in self.plays
        ):
            raise DomainValidationError("plays must contain NflversePlay values")
        if any(
            play.nflverse_game_id != self.schedule_game.nflverse_game_id
            for play in self.plays
        ):
            raise DomainValidationError(
                "mapped plays must match the schedule nflverse game ID"
            )


class NflverseSeason:
    """One reusable in-memory nflverse schedule and play snapshot."""

    __slots__ = ("_games_by_espn_id", "_plays_by_game_id", "season")

    def __init__(
        self,
        schedule: NflverseScheduleSeason,
        plays: NflversePlaySeason,
    ) -> None:
        if not isinstance(schedule, NflverseScheduleSeason):
            raise DomainValidationError(
                "schedule must be an NflverseScheduleSeason"
            )
        if not isinstance(plays, NflversePlaySeason):
            raise DomainValidationError("plays must be an NflversePlaySeason")
        if schedule.season != plays.season:
            raise DomainValidationError(
                "nflverse schedule and play seasons must match"
            )

        games_by_espn_id = {
            game.espn_id: game
            for game in schedule.games
            if game.espn_id is not None
        }
        plays_by_game_id: dict[str, list[NflversePlay]] = defaultdict(list)
        for play in plays.plays:
            plays_by_game_id[play.nflverse_game_id].append(play)

        self.season = schedule.season
        self._games_by_espn_id = games_by_espn_id
        self._plays_by_game_id = {
            game_id: tuple(game_plays)
            for game_id, game_plays in plays_by_game_id.items()
        }

    def find_game(self, espn_id: GameId) -> MappedNflverseGame:
        """Map one ESPN ID and select plays through its nflverse schedule ID."""
        if not isinstance(espn_id, str) or not espn_id.strip():
            raise DomainValidationError("espn_id must be non-empty text")
        try:
            schedule_game = self._games_by_espn_id[espn_id]
        except KeyError as exc:
            raise NflverseGameMappingError(
                espn_id=espn_id,
                season=self.season,
            ) from exc

        return MappedNflverseGame(
            schedule_game=schedule_game,
            plays=self._plays_by_game_id.get(
                schedule_game.nflverse_game_id,
                (),
            ),
        )


def load_nflverse_season(
    season: int,
    *,
    schedule_provider: NFLVerseScheduleProvider,
    play_provider: NFLVersePlayProvider,
) -> NflverseSeason:
    """Load each nflverse season collection once for one reusable run."""
    schedule = schedule_provider.load_schedule(season)
    plays = play_provider.load_plays(season)
    return NflverseSeason(schedule=schedule, plays=plays)


__all__ = [
    "MappedNflverseGame",
    "NflverseGameMappingError",
    "NflverseSeason",
    "load_nflverse_season",
]
