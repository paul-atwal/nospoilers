import type { Game, SeasonWeek, WeekInfo } from '../types';
import { revertRecord } from '../utils/records';
import {
  getCurrentNflSeason,
  getWeekInfo,
} from '../utils/scheduleWeek';
import {
  fromEspnWeek,
  toEspnWeek,
  type EspnWeek,
} from './espnWeekMapper';
import {
  collectFinalTeamResults,
  mapEspnEventToGame,
} from './espnGameMapper';
import type { EspnScoreboard } from './espnTypes';

const ESPN_API_BASE = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard';
const ODDS_CACHE_KEY = 'nfl_odds_cache';

const fetchScoreboard = async (params?: EspnWeek): Promise<EspnScoreboard> => {
  const query = params
    ? `?dates=${params.season}&week=${params.week}&seasontype=${params.seasonType}&limit=100`
    : '';
  const response = await fetch(`${ESPN_API_BASE}${query}`);
  if (!response.ok) throw new Error('Failed to fetch ESPN data');
  return response.json();
};

const getOddsCacheKey = (): string => {
  const today = new Date().toISOString().split('T')[0];
  return `${ODDS_CACHE_KEY}_${today}`;
};

const getCachedOdds = (): Record<string, string> | null => {
  try {
    const cached = localStorage.getItem(getOddsCacheKey());
    return cached ? JSON.parse(cached) : null;
  } catch (error) {
    console.error('Error reading odds cache', error);
    return null;
  }
};

const setCachedOdds = (odds: Record<string, string>): void => {
  try {
    const key = getOddsCacheKey();
    localStorage.setItem(key, JSON.stringify(odds));

    Object.keys(localStorage).forEach((storedKey) => {
      if (storedKey.startsWith(ODDS_CACHE_KEY) && storedKey !== key) {
        localStorage.removeItem(storedKey);
      }
    });
  } catch (error) {
    console.error('Error writing odds cache', error);
  }
};

const fetchOddsForUpcomingGames = async (
  currentWeek: SeasonWeek,
): Promise<Record<string, string>> => {
  const cached = getCachedOdds();
  if (cached) return cached;

  try {
    const scoreboard = await fetchScoreboard(toEspnWeek(currentWeek));
    const oddsByGameId: Record<string, string> = {};

    for (const event of scoreboard.events ?? []) {
      const status = event.status.type.state;
      const isUpcoming = status === 'pre' || status === 'scheduled';
      const odds = event.competitions[0]?.odds?.[0]?.details;
      if (isUpcoming && odds) oddsByGameId[event.id] = odds;
    }

    setCachedOdds(oddsByGameId);
    return oddsByGameId;
  } catch (error) {
    console.error('Error fetching odds', error);
    return {};
  }
};

export const fetchCurrentWeek = async (): Promise<WeekInfo> => {
  try {
    const scoreboard = await fetchScoreboard();
    const week = scoreboard.week?.number ?? 1;
    const season = scoreboard.season?.year ?? getCurrentNflSeason();
    const seasonType = scoreboard.season?.type ?? 0;
    const seasonWeek = fromEspnWeek(season, seasonType, week);
    if (!seasonWeek) throw new Error('ESPN returned an unsupported season week');
    return getWeekInfo(seasonWeek);
  } catch (error) {
    console.error('Failed to fetch current week', error);
    return getWeekInfo({
      season: getCurrentNflSeason(),
      phase: 'regular_season',
      week: 1,
    });
  }
};

const adjustRecordsForFutureWeek = async (
  games: Game[],
  viewingWeek: SeasonWeek,
  currentWeek: SeasonWeek,
): Promise<Game[]> => {
  const requiresAdjustment = currentWeek.phase === 'regular_season'
    && viewingWeek.phase === 'regular_season'
    && viewingWeek.season === currentWeek.season
    && viewingWeek.week > currentWeek.week;
  if (!requiresAdjustment) return games;

  try {
    const scoreboard = await fetchScoreboard(toEspnWeek(currentWeek));
    const results = collectFinalTeamResults(scoreboard.events ?? []);

    return games.map((game) => {
      const homeResult = results.get(game.homeTeam);
      const awayResult = results.get(game.awayTeam);

      return {
        ...game,
        homeRecord: homeResult
          ? revertRecord(game.homeRecord, homeResult)
          : game.homeRecord,
        awayRecord: awayResult
          ? revertRecord(game.awayRecord, awayResult)
          : game.awayRecord,
      };
    });
  } catch (error) {
    console.error('Error adjusting records for future week', error);
    return games;
  }
};

export const fetchSchedule = async (seasonWeek: SeasonWeek): Promise<Game[]> => {
  try {
    const espnWeek = toEspnWeek(seasonWeek);
    const scoreboard = await fetchScoreboard(espnWeek);
    const currentWeek = await fetchCurrentWeek();
    const oddsByGameId = await fetchOddsForUpcomingGames(currentWeek.seasonWeek);
    const games = (scoreboard.events ?? []).map((event) => mapEspnEventToGame(event, {
      oddsByGameId,
      seasonWeek,
    }));

    return adjustRecordsForFutureWeek(
      games,
      seasonWeek,
      currentWeek.seasonWeek,
    );
  } catch (error) {
    console.error('Error fetching NFL schedule', error);
    return [];
  }
};
