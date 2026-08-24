export type GameResult = 'win' | 'loss' | 'tie';
export type TeamSide = 'home' | 'away';

export interface RecordSummary {
  summary?: string;
}

export interface DisplayRecordInput {
  record: string;
  side: TeamSide;
  isFinal: boolean;
  isRevealed: boolean;
  isPostseason: boolean;
  homeScore: number;
  awayScore: number;
}

const parseRecord = (record: string): number[] | null => {
  if (!/^\d+-\d+(?:-\d+)?$/.test(record)) return null;
  return record.split('-').map(Number);
};

export const selectRegularSeasonRecord = (
  records: readonly RecordSummary[] | undefined,
): string => {
  if (!records?.length) return '';

  const regularSeasonRecord = records.find(({ summary }) => {
    if (!summary) return false;
    const values = parseRecord(summary);
    return values !== null && values.reduce((total, value) => total + value, 0) === 17;
  });

  return regularSeasonRecord?.summary ?? records[0]?.summary ?? '';
};

export const revertRecord = (record: string, result: GameResult): string => {
  const values = parseRecord(record);
  if (!values) return record;

  let [wins, losses, ties = 0] = values;

  if (result === 'win') wins = Math.max(0, wins - 1);
  if (result === 'loss') losses = Math.max(0, losses - 1);
  if (result === 'tie') ties = Math.max(0, ties - 1);

  return ties > 0 ? `${wins}-${losses}-${ties}` : `${wins}-${losses}`;
};

export const getDisplayRecord = ({
  record,
  side,
  isFinal,
  isRevealed,
  isPostseason,
  homeScore,
  awayScore,
}: DisplayRecordInput): string => {
  if (!isFinal || isRevealed || isPostseason) return record;

  if (homeScore === awayScore) return revertRecord(record, 'tie');

  const homeWon = homeScore > awayScore;
  const sideWon = side === 'home' ? homeWon : !homeWon;
  const result: GameResult = sideWon ? 'win' : 'loss';
  return revertRecord(record, result);
};
