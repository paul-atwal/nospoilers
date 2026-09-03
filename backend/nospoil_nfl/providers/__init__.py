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
from .espn_scoreboard import ESPN_SCOREBOARD_URL, EspnScoreboardClient
from .espn_summary import ESPN_GAME_SUMMARY_URL, EspnGameSummaryClient
from .nflverse_schedule import (
    NflverseScheduleClient,
    NflverseScheduleLoader,
    REQUIRED_SCHEDULE_COLUMNS,
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
    "ESPN_GAME_SUMMARY_URL",
    "ESPN_SCOREBOARD_URL",
    "ESPNScoreboardProvider",
    "EspnGameSummaryClient",
    "EspnScoreboardClient",
    "GameSummary",
    "NFLVersePlayProvider",
    "NFLVerseScheduleProvider",
    "NflverseGameId",
    "NflverseScheduleClient",
    "NflverseScheduleLoader",
    "NflversePlay",
    "NflversePlaySeason",
    "NflverseScheduleGame",
    "NflverseScheduleSeason",
    "ProviderDataError",
    "ProviderError",
    "ProviderTransportError",
    "ProviderUnavailableError",
    "REQUIRED_SCHEDULE_COLUMNS",
    "ScheduleGame",
    "ScheduleTeam",
    "ScoreboardBatch",
]
