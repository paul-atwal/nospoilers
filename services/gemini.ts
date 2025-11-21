
import { Game, WeekInfo } from "../types";

const ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard";
const ESPN_SUMMARY_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary";

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

/**
 * Fetches specific game summary to get Win Probability data.
 */
const fetchGameSummary = async (gameId: string) => {
  try {
    const res = await fetch(`${ESPN_SUMMARY_BASE}?event=${gameId}`);
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    console.error(`Failed to fetch summary for ${gameId}`, e);
    return null;
  }
};

/**
 * Fallback calculation if Win Probability data is missing.
 */
const calculateBasicExcitement = (homeScore: number, awayScore: number): number => {
  const diff = Math.abs(homeScore - awayScore);
  const total = homeScore + awayScore;
  
  let score = 5.0; // Baseline

  // Close game bonuses
  if (diff === 0) score += 4.0; // Tie/OT
  else if (diff <= 3) score += 3.0;
  else if (diff <= 8) score += 2.0;
  else if (diff <= 14) score += 0.5;
  else if (diff > 24) score -= 3.0; // Blowout penalty

  // High scoring bonus
  if (total > 60) score += 1.0;
  else if (total > 45) score += 0.5;
  else if (total < 24) score -= 1.0; // Defensive slugfest

  return Math.max(1.0, Math.min(10.0, score));
};

/**
 * Calculates excitement based on Win Probability (WP) History.
 */
const calculateAdvancedExcitement = (wpHistory: any[], homeScore: number, awayScore: number, statusState: string, isOT: boolean): number => {
  // If game is upcoming (pre), score is 0
  if (statusState === 'pre') return 0;

  // 1. Fallback if no history data
  if (!wpHistory || wpHistory.length === 0) {
    let basic = calculateBasicExcitement(homeScore, awayScore);
    if (isOT) basic = Math.max(basic, 8.0); // OT games are rarely boring
    return basic;
  }

  const diff = Math.abs(homeScore - awayScore);
  let weightedVolatility = 0;
  let previousWP = 0.5;
  
  // Determine Winner
  const homeWon = homeScore > awayScore;

  // Stats to track
  let minWinnerWP = 1.0; 
  let maxWinnerWP = 0.0; 
  
  // For late game analysis
  const totalSteps = wpHistory.length;
  // We consider "Late Game" to be the last 10% of the recorded plays/probabilities
  const lateGameThreshold = Math.floor(totalSteps * 0.90);
  
  let lateMinWinnerWP = 1.0; // Lowest WP the winner had in the last 10% of game
  let lateLeadChanges = 0;

  if (wpHistory.length > 0) {
    previousWP = wpHistory[0].homeWinPercentage;
    if (previousWP > 2.0) previousWP /= 100;
  }

  let garbageTimeSteps = 0;

  for (let i = 1; i < totalSteps; i++) {
    let currentWP = wpHistory[i].homeWinPercentage;
    if (currentWP > 2.0) currentWP /= 100;

    const winnerCurrentWP = homeWon ? currentWP : (1 - currentWP);
    if (winnerCurrentWP < minWinnerWP) minWinnerWP = winnerCurrentWP;
    if (winnerCurrentWP > maxWinnerWP) maxWinnerWP = winnerCurrentWP;

    // Late game specific tracking
    if (i >= lateGameThreshold) {
        if (winnerCurrentWP < lateMinWinnerWP) lateMinWinnerWP = winnerCurrentWP;
        
        // Check for lead change in late game
        // A lead change occurs if WP crosses 0.5 (ignoring exact 0.5 ties for simplicity)
        if ((previousWP - 0.5) * (currentWP - 0.5) < 0) {
            lateLeadChanges++;
        }
    }

    if (currentWP > 0.95 || currentWP < 0.05) {
      garbageTimeSteps++;
    }

    const rawDelta = Math.abs(currentWP - previousWP);

    let timeMultiplier = 1.0;
    const progress = i / totalSteps;

    // Increase multiplier for the very end of the game to reward buzzer beaters/last minute drives
    if (progress > 0.96) timeMultiplier = 15.0; // HUGE weight for final ~3-4%
    else if (progress > 0.90) timeMultiplier = 5.0;       
    else if (progress > 0.70) timeMultiplier = 3.0;  
    else if (progress < 0.25) timeMultiplier = 0.5;  

    weightedVolatility += (rawDelta * timeMultiplier);
    previousWP = currentWP;
  }

  let score = 2.0 + (weightedVolatility * 1.5);

  // --- Base Bonuses ---
  // Comeback bonus (Winner had low probability at some point)
  if (minWinnerWP <= 0.05) score += 2.0;       
  else if (minWinnerWP <= 0.15) score += 1.5;  
  else if (minWinnerWP <= 0.25) score += 1.0;  

  // Upset/Tight Game Bonus (Winner never had a "lock" until end)
  if (maxWinnerWP < 0.90) { // Winner never hit 90% until very end
      score += 1.0;
  }

  // --- Late Game Specific Bonuses ("Up for Grabs" Factor) ---
  // If the game was still in doubt (Winner WP < 60%) in the final 10% of ticks
  if (lateMinWinnerWP < 0.60) score += 1.5;
  
  // If the winner was actually LOSING (WP < 50%) in the final 10% (Late Comeback)
  if (lateMinWinnerWP < 0.50) score += 1.5; // Cumulative with above -> +3.0 total for late comebacks

  // Late Lead Changes are gold
  if (lateLeadChanges > 0) score += 1.0 + (lateLeadChanges * 0.5);

  // Close Score Bonus
  if (diff <= 3) score += 1.0;
  else if (diff <= 7) score += 0.5;
  if (diff > 16) score -= 2.0; 

  // Garbage Time Penalty
  if (garbageTimeSteps / totalSteps > 0.5) {
    score -= 2.0;
  }
  
  // High Score Bonus
  const totalPoints = homeScore + awayScore;
  if (totalPoints > 60) score += 0.5;

  // --- Overtime Bonus ---
  // OT games are inherently exciting.
  if (isOT) {
      score += 2.0;
  }

  return Math.max(1.0, Math.min(10.0, score));
};


export const fetchCurrentWeek = async (): Promise<WeekInfo> => {
  try {
    const response = await fetch(ESPN_API_BASE);
    const data = await response.json();
    const apiWeek = data.week?.number || 1;
    const apiSeasonType = data.season?.type || 2;
    
    // Convert API params back to our continuous week format for state
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

export const fetchGamesForWeek = async (continuousWeek: number): Promise<Game[]> => {
  try {
    const { seasonType, week } = getApiParams(continuousWeek);
    
    const response = await fetch(`${ESPN_API_BASE}?week=${week}&seasontype=${seasonType}&limit=100`);
    if (!response.ok) throw new Error("Failed to fetch ESPN data");
    
    const data = await response.json();
    const events = data.events || [];
    
    const weekLabel = getWeekLabel(week, seasonType);

    const gamePromises = events.map(async (event: any) => {
      const competition = event.competitions[0];
      const home = competition.competitors.find((c: any) => c.homeAway === 'home');
      const away = competition.competitors.find((c: any) => c.homeAway === 'away');
      
      const statusState = event.status.type.state; 
      let statusDetail = event.status.type.shortDetail;
      
      // Detect OT before stripping it
      const isOT = statusDetail.includes("OT") || statusDetail.includes("Overtime");

      // Fix: Remove /OT from Final status for clean display
      if (statusDetail.includes("Final") || statusDetail.includes("FINAL")) {
        statusDetail = "Final";
      }

      // Fix: For upcoming games, statusDetail is often "11/23 - 1:00 PM EST". 
      // We want to clean this so we can use our own formatting.
      if (statusState === 'pre') {
        statusDetail = "Upcoming";
      }

      const homeScore = parseInt(home.score || '0');
      const awayScore = parseInt(away.score || '0');
      
      const homeRecord = home.records?.[0]?.summary || '';
      const awayRecord = away.records?.[0]?.summary || '';

      const isUpcoming = statusState === 'pre';
      const isLive = statusState === 'in';

      let excitementScore = 0;

      if (!isUpcoming) {
        const summaryData = await fetchGameSummary(event.id);
        const wpHistory = summaryData?.winProbability || [];
        excitementScore = calculateAdvancedExcitement(wpHistory, homeScore, awayScore, statusState, isOT);
      }

      const dateObj = new Date(event.date);
      const timeZone = 'America/Los_Angeles'; // Changed to PST
      const dayOfWeek = dateObj.toLocaleDateString('en-US', { weekday: 'short', timeZone }).toUpperCase();
      const dateLabel = dateObj.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', timeZone });
      const kickoffTime = dateObj.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZoneName: 'short', timeZone });

      const odds = competition.odds?.[0]?.details;

      return {
        id: event.id,
        homeTeam: home.team.shortDisplayName || home.team.displayName,
        awayTeam: away.team.shortDisplayName || away.team.displayName,
        homeTeamLogo: home.team.logo,
        awayTeamLogo: away.team.logo,
        homeScore,
        awayScore,
        homeRecord,
        awayRecord,
        status: statusDetail,
        kickoffTime,
        dayOfWeek,
        dateLabel,
        weekLabel, // Added for Top 5 View
        excitementScore,
        isUpcoming,
        isLive,
        odds,
        broadcaster: competition.broadcasts?.[0]?.names?.[0],
        spoilerData: {
          homeScore: home.score || '0',
          awayScore: away.score || '0',
          summary: isUpcoming 
            ? `Kickoff at ${kickoffTime}`
            : `${away.team.abbreviation} ${away.score} @ ${home.team.abbreviation} ${home.score}`
        }
      };
    });

    return await Promise.all(gamePromises);

  } catch (error) {
    console.error("Error fetching NFL games:", error);
    return [];
  }
};
