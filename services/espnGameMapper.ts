import type {
  Game,
  GameRecordSnapshots,
  RecordScope,
  SeasonWeek,
} from '../types';
import {
  buildPostseasonRecordSnapshots,
  buildRecordSnapshots,
  deriveAddedResults,
  getTeamResult,
  revertGameResult,
  selectCurrentTeamRecord,
  type GameResult,
  selectRegularSeasonTeamRecord,
} from '../utils/records';
import type { EspnCompetitor, EspnEvent } from './espnTypes';

export interface EspnGameMappingContext {
  oddsByGameId: Readonly<Record<string, string>>;
  seasonWeek: SeasonWeek;
}

const getCompetitor = (
  competitors: EspnCompetitor[],
  side: 'home' | 'away',
): EspnCompetitor => {
  const competitor = competitors.find(({ homeAway }) => homeAway === side);
  if (!competitor) throw new Error(`ESPN event is missing its ${side} competitor`);
  return competitor;
};

const getRecordScope = (seasonWeek: SeasonWeek): RecordScope => {
  if (seasonWeek.phase === 'preseason') return 'preseason';
  if (seasonWeek.phase === 'regular_season') return 'regular_season';
  return 'season_to_date';
};

const prepareRecordSnapshots = (
  competitor: EspnCompetitor,
  seasonWeek: SeasonWeek,
  currentResult?: GameResult,
): GameRecordSnapshots | null => {
  const sourceRecord = selectCurrentTeamRecord(competitor.records);
  if (!sourceRecord) return null;

  const pregameRecord = currentResult
    ? revertGameResult(sourceRecord, currentResult)
    : sourceRecord;

  if (seasonWeek.phase !== 'postseason') {
    return buildRecordSnapshots(
      pregameRecord,
      getRecordScope(seasonWeek),
      currentResult,
    );
  }

  const regularSeasonRecord = selectRegularSeasonTeamRecord(competitor.records);
  if (regularSeasonRecord) {
    const priorPostseasonResults = deriveAddedResults(
      regularSeasonRecord,
      pregameRecord,
    );
    if (priorPostseasonResults) {
      return buildPostseasonRecordSnapshots(
        regularSeasonRecord,
        priorPostseasonResults,
        currentResult,
      );
    }
  }

  return buildRecordSnapshots(
    pregameRecord,
    'season_to_date',
    currentResult,
  );
};

export const mapEspnEventToGame = (
  event: EspnEvent,
  { oddsByGameId, seasonWeek }: EspnGameMappingContext,
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
  const homeResult = isFinal
    ? getTeamResult('home', homeScore, awayScore)
    : undefined;
  const awayResult = isFinal
    ? getTeamResult('away', homeScore, awayScore)
    : undefined;
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
    homeRecord: prepareRecordSnapshots(home, seasonWeek, homeResult),
    awayRecord: prepareRecordSnapshots(away, seasonWeek, awayResult),
    status,
    kickoffTime,
    dayOfWeek: date.toLocaleDateString('en-US', { weekday: 'short', timeZone }).toUpperCase(),
    dateLabel: date.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', timeZone }),
    seasonWeek,
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

      const opponent = competitors.find(({ id }) => id !== competitor.id);
      if (!opponent) continue;

      const home = competitor.homeAway === 'home' ? competitor : opponent;
      const away = competitor.homeAway === 'away' ? competitor : opponent;
      const homeScore = Number.parseInt(home.score ?? '0', 10);
      const awayScore = Number.parseInt(away.score ?? '0', 10);
      const result = getTeamResult(
        competitor.homeAway,
        homeScore,
        awayScore,
      );

      results.set(teamName, result);
    }
  }

  return results;
};
