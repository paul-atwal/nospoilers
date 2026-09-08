import type { TeamRecord } from '../types';

export const formatTeamRecord = (record: TeamRecord): string => (
  record.ties > 0
    ? `${record.wins}-${record.losses}-${record.ties}`
    : `${record.wins}-${record.losses}`
);
