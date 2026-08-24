import type { Game, SupportedSeasonType } from '../types';
import {
  type GameResult,
  selectRegularSeasonRecord,
} from '../utils/records';
import type { EspnCompetitor, EspnEvent } from './espnTypes';

export interface EspnGameMappingContext {
  oddsByGameId: Readonly<Record<string, string>>;
  seasonType: SupportedSeasonType;
  weekLabel: string;
}

const getCompetitor = (
  competitors: EspnCompetitor[],
  side: 'home' | 'away',
): EspnCompetitor => {
  const competitor = competitors.find(({ homeAway }) => homeAway === side);
  if (!competitor) throw new Error(`ESPN event is missing its ${side} competitor`);
  return competitor;
};

export const mapEspnEventToGame = (
  event: EspnEvent,
  { oddsByGameId, seasonType, weekLabel }: EspnGameMappingContext,
): Game => {
  const competition = event.competitions[0];
  if (!competition) throw new Error(`ESPN event ${event.id} has no competition`);

  const home = getCompetitor(competition.competitors, 'home');
  const away = getCompetitor(competition.competitors, 'away');
  const statusState = event.status.type.state;
  const isLive = statusState === 'in';
  const isFinal = statusState === 'post';
  const isUpcoming = statusState === 'pre'
    || statusState === 'scheduled'
    || (!isLive && !isFinal);

  let status = event.status.type.shortDetail;
  if (/final/i.test(status)) status = 'Final';
  if (isUpcoming) status = 'Upcoming';

  const homeScore = Number.parseInt(home.score ?? '0', 10);
  const awayScore = Number.parseInt(away.score ?? '0', 10);
  const date = new Date(event.date);
  const timeZone = 'America/Los_Angeles';
  const kickoffTime = date.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone,
    timeZoneName: 'short',
  });

  return {
    id: event.id,
    homeTeam: home.team.shortDisplayName || home.team.displayName || '',
    awayTeam: away.team.shortDisplayName || away.team.displayName || '',
    homeTeamLogo: home.team.logo,
    awayTeamLogo: away.team.logo,
    homeScore,
    awayScore,
    homeRecord: selectRegularSeasonRecord(home.records),
    awayRecord: selectRegularSeasonRecord(away.records),
    status,
    kickoffTime,
    dayOfWeek: date.toLocaleDateString('en-US', { weekday: 'short', timeZone }).toUpperCase(),
    dateLabel: date.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', timeZone }),
    weekLabel,
    seasonType,
    excitementScore: isUpcoming ? 0 : null,
    isEstimated: false,
    isUpcoming,
    isLive,
    odds: isUpcoming ? oddsByGameId[event.id] : undefined,
    broadcaster: competition.broadcasts?.[0]?.names?.[0],
    spoilerData: {
      homeScore: home.score ?? '0',
      awayScore: away.score ?? '0',
      summary: isUpcoming
        ? `Kickoff at ${kickoffTime}`
        : `${away.team.abbreviation} ${away.score} @ ${home.team.abbreviation} ${home.score}`,
    },
  };
};

export const collectFinalTeamResults = (
  events: readonly EspnEvent[],
): Map<string, GameResult> => {
  const results = new Map<string, GameResult>();

  for (const event of events) {
    if (event.status.type.state !== 'post') continue;

    const competitors = event.competitions[0]?.competitors ?? [];
    for (const competitor of competitors) {
      const teamName = competitor.team.shortDisplayName || competitor.team.displayName;
      if (!teamName) continue;

      const score = Number.parseInt(competitor.score ?? '0', 10);
      const opponent = competitors.find(({ id }) => id !== competitor.id);
      const opponentScore = Number.parseInt(opponent?.score ?? '0', 10);
      const result: GameResult = score === opponentScore
        ? 'tie'
        : score > opponentScore ? 'win' : 'loss';

      results.set(teamName, result);
    }
  }

  return results;
};
