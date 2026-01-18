
import { Game, WeekInfo } from "../types";

const ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard";
const BACKEND_API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

/**
 * Extract regular season record from ESPN records array.
 * During playoffs, ESPN may include playoff games which makes records inconsistent.
 * We look for the record that sums to 17 games (regular season), or fall back to first record.
 */
const getRegularSeasonRecord = (records: any[] | undefined): string => {
  if (!records || records.length === 0) return '';

  // Try to find a record that sums to 17 (full regular season)
  for (const record of records) {
    if (record.summary) {
      const parts = record.summary.split('-').map((s: string) => parseInt(s, 10));
      if (parts.length >= 2 && !parts.some(isNaN)) {
        const totalGames = parts.reduce((a: number, b: number) => a + b, 0);
        if (totalGames === 17) {
          return record.summary;
        }
      }
    }
  }

  // Fall back to first record
  return records[0]?.summary || '';
};

// Odds caching
const ODDS_CACHE_KEY = "nfl_odds_cache";
const getOddsCacheKey = () => {
  const today = new Date().toISOString().split('T')[0]; // YYYY-MM-DD
  return `${ODDS_CACHE_KEY}_${today}`;
};

const getCachedOdds = (): Record<string, string> | null => {
  try {
    const key = getOddsCacheKey();
    const cached = localStorage.getItem(key);
    if (cached) {
      return JSON.parse(cached);
    }
  } catch (e) {
    console.error("Error reading odds cache", e);
  }
  return null;
};

const setCachedOdds = (odds: Record<string, string>) => {
  try {
    const key = getOddsCacheKey();
    localStorage.setItem(key, JSON.stringify(odds));
    
    // Clean up old cache entries (older than today)
    const today = new Date().toISOString().split('T')[0];
    Object.keys(localStorage).forEach(k => {
      if (k.startsWith(ODDS_CACHE_KEY) && !k.endsWith(today)) {
        localStorage.removeItem(k);
      }
    });
  } catch (e) {
    console.error("Error writing odds cache", e);
  }
};

/**
 * Fetch odds for upcoming games in the current week.
 * Caches results for the day.
 */
const fetchOddsForUpcomingGames = async (currentWeek: number): Promise<Record<string, string>> => {
  // Check cache first
  const cached = getCachedOdds();
  if (cached) {
    console.log("Using cached odds for today");
    return cached;
  }

  console.log("Fetching fresh odds for upcoming games");
  
  try {
    const { seasonType, week } = getApiParams(currentWeek);
    const response = await fetch(`${ESPN_API_BASE}?week=${week}&seasontype=${seasonType}&limit=100`);
    
    if (!response.ok) return {};
    
    const data = await response.json();
    const events = data.events || [];
    const oddsMap: Record<string, string> = {};
    
    for (const event of events) {
      const statusState = event.status?.type?.state;
      const isUpcoming = statusState === 'pre' || statusState === 'scheduled';
      
      if (isUpcoming) {
        const competition = event.competitions[0];
        const odds = competition.odds?.[0]?.details;
        if (odds) {
          oddsMap[event.id] = odds;
        }
      }
    }
    
    setCachedOdds(oddsMap);
    return oddsMap;
  } catch (e) {
    console.error("Error fetching odds", e);
    return {};
  }
};

/**
 * Helper to map continuous week numbers (19+) to API SeasonType/Week params
 */
const getApiParams = (continuousWeek: number) => {
  if (continuousWeek <= 18) {
    return { seasonType: 2, week: continuousWeek };
  }
  // Week 19 -> Post Week 1, etc.
  return { seasonType: 3, week: continuousWeek - 18 };
};

/**
 * Helper to get display label for a week
 */
export const getWeekLabel = (week: number, seasonType: number): string => {
  if (seasonType === 2) return `Week ${week}`;
  if (seasonType === 3) {
    if (week === 1) return "Wild Card";
    if (week === 2) return "Divisional Round";
    if (week === 3) return "Championship Round";
    if (week === 4) return "Pro Bowl"; 
    if (week === 5) return "Super Bowl";
    return "Postseason";
  }
  return `Week ${week}`;
};

export const fetchCurrentWeek = async (): Promise<WeekInfo> => {
  try {
    const response = await fetch(ESPN_API_BASE);
    const data = await response.json();
    const apiWeek = data.week?.number || 1;
    const apiSeasonType = data.season?.type || 2;
    
    let continuousWeek = apiWeek;
    if (apiSeasonType === 3) {
        continuousWeek = apiWeek + 18;
    }

    return {
      week: continuousWeek,
      seasonType: apiSeasonType,
      label: getWeekLabel(apiWeek, apiSeasonType)
    };
  } catch (e) {
    console.error("Failed to fetch current week", e);
    return { week: 1, seasonType: 2, label: "Week 1" };
  }
};

/**
 * Helper to revert a record based on game result
 */
const revertRecord = (record: string, result: 'win' | 'loss' | 'tie'): string => {
  if (!record) return '';
  
  const parts = record.split('-').map(s => parseInt(s, 10));
  if (parts.some(isNaN)) return record;

  let [w, l, t] = parts;

  if (result === 'win') w = Math.max(0, w - 1);
  if (result === 'loss') l = Math.max(0, l - 1);
  if (result === 'tie') t = Math.max(0, (t || 0) - 1);

  if (t && t > 0) return `${w}-${l}-${t}`;
  return `${w}-${l}`;
};

/**
 * Adjusts team records for future weeks to prevent spoilers.
 * When viewing a future week while current week is ongoing,
 * reverts records to pre-current-week values.
 * Only active during regular season (weeks 1-18).
 * DISABLED during playoffs - records are final regular season records.
 */
const adjustRecordsForFutureWeek = async (
  games: Game[],
  viewingWeek: number,
  currentWeek: number
): Promise<Game[]> => {
  // PLAYOFFS: Skip all record adjustment when we're in playoffs
  // currentWeek > 18 means we're in postseason, records are final
  if (currentWeek > 18) {
    return games;
  }

  // REGULAR SEASON: Skip if viewing playoff weeks or current/past weeks
  if (viewingWeek > 18 || viewingWeek <= currentWeek) {
    return games;
  }

  try {
    // Fetch current week's games to see results
    const { seasonType: currentSeasonType, week: currentApiWeek } = getApiParams(currentWeek);
    const currentWeekResponse = await fetch(
      `${ESPN_API_BASE}?week=${currentApiWeek}&seasontype=${currentSeasonType}&limit=100`
    );
    
    if (!currentWeekResponse.ok) return games;
    
    const currentWeekData = await currentWeekResponse.json();
    const currentWeekEvents = currentWeekData.events || [];
    
    // Build a map of team -> result from current week
    const teamResults = new Map<string, 'win' | 'loss' | 'tie'>();
    
    for (const event of currentWeekEvents) {
      const statusState = event.status?.type?.state;
      const isFinal = statusState === 'post';
      
      // Only process completed games from current week
      if (!isFinal) continue;
      
      const competition = event.competitions[0];
      const competitors = competition?.competitors || [];
      
      for (const competitor of competitors) {
        const teamName = competitor.team?.shortDisplayName || competitor.team?.displayName;
        if (!teamName) continue;
        
        const score = parseInt(competitor.score || '0');
        const opponentScore = parseInt(
          competitors.find((c: any) => c.id !== competitor.id)?.score || '0'
        );
        
        let result: 'win' | 'loss' | 'tie';
        if (score > opponentScore) result = 'win';
        else if (score < opponentScore) result = 'loss';
        else result = 'tie';
        
        teamResults.set(teamName, result);
      }
    }
    
    // Adjust records for teams that played in current week
    return games.map(game => {
      const homeResult = teamResults.get(game.homeTeam);
      const awayResult = teamResults.get(game.awayTeam);
      
      const adjustedHomeRecord = homeResult 
        ? revertRecord(game.homeRecord, homeResult)
        : game.homeRecord;
        
      const adjustedAwayRecord = awayResult
        ? revertRecord(game.awayRecord, awayResult)
        : game.awayRecord;
      
      return {
        ...game,
        homeRecord: adjustedHomeRecord,
        awayRecord: adjustedAwayRecord
      };
    });
    
  } catch (e) {
    console.error('Error adjusting records for future week:', e);
    return games; // Return unchanged on error
  }
};

/**
 * Fetches schedule from ESPN (scores and status only, no win probability)
 */
export const fetchSchedule = async (continuousWeek: number): Promise<Game[]> => {
  try {
    const { seasonType, week } = getApiParams(continuousWeek);
    
    const response = await fetch(`${ESPN_API_BASE}?week=${week}&seasontype=${seasonType}&limit=100`);
    if (!response.ok) throw new Error("Failed to fetch ESPN data");
    
    const data = await response.json();
    const events = data.events || [];
    const weekLabel = getWeekLabel(week, seasonType);

    // Fetch odds for only upcoming games (cached daily)
    const currentWeekInfo = await fetchCurrentWeek();
    const oddsMap = await fetchOddsForUpcomingGames(currentWeekInfo.week);

    const games = events.map((event: any) => {
        const competition = event.competitions[0];
        const home = competition.competitors.find((c: any) => c.homeAway === 'home');
        const away = competition.competitors.find((c: any) => c.homeAway === 'away');
        
        const statusState = event.status.type.state; 
        let statusDetail = event.status.type.shortDetail;
        
        const isOT = statusDetail.includes("OT") || statusDetail.includes("Overtime");
        if (statusDetail.includes("Final") || statusDetail.includes("FINAL")) statusDetail = "Final";

        const isLive = statusState === 'in';
        const isFinal = statusState === 'post';
        const isUpcoming = statusState === 'pre' || statusState === 'scheduled' || (!isLive && !isFinal);

        if (isUpcoming) statusDetail = "Upcoming";

        const homeScore = parseInt(home.score || '0');
        const awayScore = parseInt(away.score || '0');
        
        const dateObj = new Date(event.date);
        const timeZone = 'America/Los_Angeles';
        
        const dayOfWeek = dateObj.toLocaleDateString('en-US', { weekday: 'short', timeZone }).toUpperCase();
        const dateLabel = dateObj.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', timeZone });
        const kickoffTime = dateObj.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZoneName: 'short', timeZone });

        // Use cached odds for upcoming games
        const odds = isUpcoming ? oddsMap[event.id] : undefined;

        return {
            id: event.id,
            homeTeam: home.team.shortDisplayName || home.team.displayName,
            awayTeam: away.team.shortDisplayName || away.team.displayName,
            homeTeamLogo: home.team.logo,
            awayTeamLogo: away.team.logo,
            homeScore,
            awayScore,
            // For playoffs, show only regular season record (17 games total)
            // ESPN provides inconsistent playoff records, so we extract just the reg season portion
            homeRecord: getRegularSeasonRecord(home.records),
            awayRecord: getRegularSeasonRecord(away.records),
            status: statusDetail,
            kickoffTime,
            dayOfWeek,
            dateLabel,
            weekLabel,
            // Excitement score will be fetched from backend
            // null = not yet calculated, 0 = upcoming (no score)
            excitementScore: isUpcoming ? 0 : null, 
            isEstimated: false,
            isUpcoming,
            isLive,
            odds,
            broadcaster: competition.broadcasts?.[0]?.names?.[0],
            spoilerData: {
                homeScore: home.score || '0',
                awayScore: away.score || '0',
                summary: isUpcoming ? `Kickoff at ${kickoffTime}` : `${away.team.abbreviation} ${away.score} @ ${home.team.abbreviation} ${home.score}`
            }
        };
    });

    // Adjust records for future weeks to prevent spoilers
    return await adjustRecordsForFutureWeek(games, continuousWeek, currentWeekInfo.week);

  } catch (error) {
    console.error("Error fetching NFL schedule:", error);
    return [];
  }
};

/**
 * Fetches excitement score from FastAPI backend
 */
export const fetchGameExcitement = async (game: Game): Promise<{ score: number | null, isEstimated: boolean }> => {
    // No excitement for upcoming or live games
    if (game.isUpcoming || game.isLive) {
        return { score: null, isEstimated: false };
    }

    try {
        const response = await fetch(`${BACKEND_API_BASE}/excitement/${game.id}`, { cache: 'no-store' });
        
        if (!response.ok) {
            // Backend doesn't have data yet - return -1 to indicate "checked but no data"
            // This prevents the queue from infinitely retrying this game
            return { score: -1, isEstimated: false };
        }
        
        const data = await response.json();
        return { 
            score: data.excitement_score,
            isEstimated: false
        };
    } catch (e) {
        console.error(`Error fetching excitement for ${game.id}`, e);
        // Return -1 to indicate checked but failed
        return { score: -1, isEstimated: false };
    }
};

// Backwards compatibility wrapper
export const fetchGamesForWeek = async (continuousWeek: number): Promise<Game[]> => {
    const games = await fetchSchedule(continuousWeek);
    return games;
};
