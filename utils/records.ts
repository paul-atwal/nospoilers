import type { TeamRecord } from '../types';

export interface RecordSummary {
  name?: string;
  summary?: string;
  type?: string;
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

export const selectOverallTeamRecord = (
  records: readonly RecordSummary[] | undefined,
): TeamRecord | null => {
  if (!records?.length) return null;

  const overallRecord = records.find(({ name, summary, type }) => {
    if (name !== 'overall' || type !== 'total' || !summary) return false;
    return parseTeamRecord(summary) !== null;
  });

  return overallRecord?.summary
    ? parseTeamRecord(overallRecord.summary)
    : null;
};
