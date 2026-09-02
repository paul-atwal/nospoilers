"""Public contracts and values for external data providers."""

from .contracts import (
    ESPNGameSummaryProvider,
    ESPNScoreboardProvider,
    NFLVersePlayProvider,
    NFLVerseScheduleProvider,
)
from .errors import (
    ProviderDataError,
    ProviderError,
    ProviderTransportError,
    ProviderUnavailableError,
)
from .models import (
    GameSummary,
    NflverseGameId,
    NflversePlay,
    NflversePlaySeason,
    NflverseScheduleGame,
    NflverseScheduleSeason,
    ScheduleGame,
    ScheduleTeam,
    ScoreboardBatch,
)

__all__ = [
    "ESPNGameSummaryProvider",
    "ESPNScoreboardProvider",
    "GameSummary",
    "NFLVersePlayProvider",
    "NFLVerseScheduleProvider",
    "NflverseGameId",
    "NflversePlay",
    "NflversePlaySeason",
    "NflverseScheduleGame",
    "NflverseScheduleSeason",
    "ProviderDataError",
    "ProviderError",
    "ProviderTransportError",
    "ProviderUnavailableError",
    "ScheduleGame",
    "ScheduleTeam",
    "ScoreboardBatch",
]
