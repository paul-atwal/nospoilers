import type {
  GameRecordSnapshots,
  RecordScope,
  RecordSnapshot,
  TeamRecord,
} from '../types';

export type GameResult = 'win' | 'loss' | 'tie';
export type TeamSide = 'home' | 'away';

export interface RecordSummary {
  summary?: string;
}

export const parseTeamRecord = (record: string): TeamRecord | null => {
  if (!/^\d+-\d+(?:-\d+)?$/.test(record)) return null;
  const [wins, losses, ties = 0] = record.split('-').map(Number);
  return { wins, losses, ties };
};

export const formatTeamRecord = (record: TeamRecord): string => (
  record.ties > 0
    ? `${record.wins}-${record.losses}-${record.ties}`
    : `${record.wins}-${record.losses}`
);

export const applyGameResult = (
  record: TeamRecord,
  result: GameResult,
): TeamRecord => ({
  wins: record.wins + (result === 'win' ? 1 : 0),
  losses: record.losses + (result === 'loss' ? 1 : 0),
  ties: record.ties + (result === 'tie' ? 1 : 0),
});

export const revertGameResult = (
  record: TeamRecord,
  result: GameResult,
): TeamRecord => ({
  wins: Math.max(0, record.wins - (result === 'win' ? 1 : 0)),
  losses: Math.max(0, record.losses - (result === 'loss' ? 1 : 0)),
  ties: Math.max(0, record.ties - (result === 'tie' ? 1 : 0)),
});

export const getTeamResult = (
  side: TeamSide,
  homeScore: number,
  awayScore: number,
): GameResult => {
  if (homeScore === awayScore) return 'tie';
  const homeWon = homeScore > awayScore;
  return (side === 'home') === homeWon ? 'win' : 'loss';
};

export const deriveAddedResults = (
  baseRecord: TeamRecord,
  cumulativeRecord: TeamRecord,
): GameResult[] | null => {
  const addedWins = cumulativeRecord.wins - baseRecord.wins;
  const addedLosses = cumulativeRecord.losses - baseRecord.losses;
  const addedTies = cumulativeRecord.ties - baseRecord.ties;
  if (addedWins < 0 || addedLosses < 0 || addedTies < 0) return null;

  return [
    ...Array<GameResult>(addedWins).fill('win'),
    ...Array<GameResult>(addedLosses).fill('loss'),
    ...Array<GameResult>(addedTies).fill('tie'),
  ];
};

const makeRecordSnapshot = (
  record: TeamRecord,
  scope: RecordScope,
): RecordSnapshot => ({
  record: { ...record },
  scope,
});

export const buildRecordSnapshots = (
  pregameRecord: TeamRecord,
  scope: RecordScope,
  currentResult?: GameResult,
): GameRecordSnapshots => {
  const pregame = makeRecordSnapshot(pregameRecord, scope);
  if (!currentResult) return { pregame };

  return {
    pregame,
    postgame: makeRecordSnapshot(
      applyGameResult(pregameRecord, currentResult),
      scope,
    ),
  };
};

export const buildPostseasonRecordSnapshots = (
  regularSeasonRecord: TeamRecord,
  priorPostseasonResults: readonly GameResult[],
  currentResult?: GameResult,
): GameRecordSnapshots => {
  const pregameRecord = priorPostseasonResults.reduce(
    applyGameResult,
    regularSeasonRecord,
  );
  return buildRecordSnapshots(
    pregameRecord,
    'season_to_date',
    currentResult,
  );
};

export const revertPregameSnapshot = (
  snapshots: GameRecordSnapshots | null,
  result: GameResult,
): GameRecordSnapshots | null => {
  if (!snapshots) return null;

  return {
    ...snapshots,
    pregame: {
      ...snapshots.pregame,
      record: revertGameResult(snapshots.pregame.record, result),
    },
  };
};

const getRecordTotal = (record: TeamRecord): number => (
  record.wins + record.losses + record.ties
);

export const selectRegularSeasonTeamRecord = (
  records: readonly RecordSummary[] | undefined,
): TeamRecord | null => {
  if (!records?.length) return null;

  const regularSeasonRecord = records.find(({ summary }) => {
    if (!summary) return false;
    const record = parseTeamRecord(summary);
    return record !== null && getRecordTotal(record) === 17;
  });

  return regularSeasonRecord?.summary
    ? parseTeamRecord(regularSeasonRecord.summary)
    : null;
};

export const selectCurrentTeamRecord = (
  records: readonly RecordSummary[] | undefined,
): TeamRecord | null => {
  if (!records?.length) return null;

  for (const { summary } of records) {
    if (!summary) continue;
    const record = parseTeamRecord(summary);
    if (record) return record;
  }
  return null;
};
