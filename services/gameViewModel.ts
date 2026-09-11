import type { ApiGame, ApiRecord, ApiTeam, Game, GameRecordSnapshots, RatingPresentation, TeamView, KickoffView } from '../types';
import { getTeamDisplayName, getTeamFallbackLabel, getTeamLogoUrl } from './teamAssets';

const STATUS_LABELS: Record<ApiGame['status']['state'], string> = {
  scheduled: 'Scheduled', in_progress: 'In Progress', final: 'Final', delayed: 'Delayed', postponed: 'Postponed', cancelled: 'Cancelled',
};

export const getViewerTimeZone = (): string => (
  Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
);

export const formatKickoff = (kickoffAt: string | null, timeZone = getViewerTimeZone()): KickoffView => {
  if (!kickoffAt || !Number.isFinite(Date.parse(kickoffAt))) return { time: 'Kickoff time TBD', day: '', date: '', zone: '' };
  const date = new Date(kickoffAt);
  const options = { timeZone };
  const parts = new Intl.DateTimeFormat(undefined, { ...options, hour: 'numeric', minute: '2-digit', timeZoneName: 'shortGeneric' }).formatToParts(date);
  const zone = parts.find((part) => part.type === 'timeZoneName')?.value ?? '';
  return {
    time: new Intl.DateTimeFormat(undefined, { ...options, hour: 'numeric', minute: '2-digit' }).format(date),
    day: new Intl.DateTimeFormat(undefined, { ...options, weekday: 'short' }).format(date).toUpperCase(),
    date: new Intl.DateTimeFormat(undefined, { ...options, month: 'numeric', day: 'numeric' }).format(date),
    zone,
  };
};

const toSnapshots = (team: ApiTeam): GameRecordSnapshots | null => {
  if (!team.pregameRecord) return null;
  const convert = (record: ApiRecord) => ({ record: { wins: record.wins, losses: record.losses, ties: record.ties }, scope: record.scope });
  return team.postgameRecord ? { pregame: convert(team.pregameRecord), postgame: convert(team.postgameRecord) } : { pregame: convert(team.pregameRecord) };
};

const toTeam = (team: ApiTeam): TeamView => ({
  id: team.id,
  name: getTeamDisplayName(team),
  abbreviation: getTeamFallbackLabel(team),
  logoUrl: getTeamLogoUrl(team),
  records: toSnapshots(team),
});

export const toRatingPresentation = (game: ApiGame): RatingPresentation => {
  const score = game.rating.state === 'confirmed' || game.rating.state === 'provisional' ? game.rating.score : null;
  const labels: Record<ApiGame['rating']['state'], string> = { pending: 'Rating pending', provisional: 'Provisional rating', confirmed: 'Confirmed rating', unavailable: 'Rating unavailable' };
  return {
    state: game.rating.state,
    label: labels[game.rating.state],
    score,
    confirmationSupported: game.rating.confirmationSupported,
  };
};

export const toViewGame = (apiGame: ApiGame, timeZone = getViewerTimeZone()): Game => {
  const kickoff = formatKickoff(apiGame.kickoffAt, timeZone);
  const score = apiGame.status.score;
  const hasScore = score !== null && Number.isFinite(score.home) && Number.isFinite(score.away);
  const home = toTeam(apiGame.home);
  const away = toTeam(apiGame.away);
  const rating = toRatingPresentation(apiGame);
  const isUpcoming = apiGame.status.state === 'scheduled' || apiGame.status.state === 'postponed' || apiGame.status.state === 'cancelled';
  return {
    id: apiGame.id,
    homeTeam: home.name,
    awayTeam: away.name,
    homeTeamLogo: home.logoUrl ?? undefined,
    awayTeamLogo: away.logoUrl ?? undefined,
    homeScore: hasScore ? score.home : null,
    awayScore: hasScore ? score.away : null,
    homeRecord: home.records,
    awayRecord: away.records,
    status: apiGame.status.detail ?? STATUS_LABELS[apiGame.status.state],
    kickoffTime: kickoff.time,
    dayOfWeek: kickoff.day,
    dateLabel: kickoff.date,
    kickoff,
    home,
    away,
    seasonWeek: apiGame.seasonWeek,
    rating,
    excitementScore: rating.score,
    isEstimated: rating.state === 'provisional',
    spoilerData: { homeScore: hasScore ? score.home : null, awayScore: hasScore ? score.away : null, summary: '' },
    broadcaster: apiGame.broadcaster ?? undefined,
    isUpcoming,
    isScheduled: apiGame.status.state === 'scheduled',
    isLive: apiGame.status.state === 'in_progress' || apiGame.status.state === 'delayed',
    odds: apiGame.odds?.details ?? undefined,
  };
};
