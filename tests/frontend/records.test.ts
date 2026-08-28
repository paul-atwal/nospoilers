import { describe, expect, it } from 'vitest';
import {
  applyGameResult,
  buildPostseasonRecordSnapshots,
  buildRecordSnapshots,
  deriveAddedResults,
  formatTeamRecord,
  parseTeamRecord,
  revertGameResult,
  revertPregameSnapshot,
  selectCurrentTeamRecord,
  selectRegularSeasonTeamRecord,
} from '../../utils/records';

describe('structured team records', () => {
  it('parses ESPN display records without losing ties', () => {
    expect(parseTeamRecord('12-5')).toEqual({
      wins: 12,
      losses: 5,
      ties: 0,
    });
    expect(parseTeamRecord('8-2-1')).toEqual({
      wins: 8,
      losses: 2,
      ties: 1,
    });
    expect(parseTeamRecord('unknown')).toBeNull();
  });

  it('formats zero ties only when the value is meaningful', () => {
    expect(formatTeamRecord({ wins: 12, losses: 5, ties: 0 })).toBe('12-5');
    expect(formatTeamRecord({ wins: 8, losses: 2, ties: 1 })).toBe('8-2-1');
  });

  it.each([
    ['win', { wins: 9, losses: 2, ties: 0 }],
    ['loss', { wins: 8, losses: 3, ties: 0 }],
    ['tie', { wins: 8, losses: 2, ties: 1 }],
  ] as const)('applies a %s without changing the input record', (result, expected) => {
    const original = { wins: 8, losses: 2, ties: 0 };

    expect(applyGameResult(original, result)).toEqual(expected);
    expect(original).toEqual({ wins: 8, losses: 2, ties: 0 });
  });
});

describe('record snapshots', () => {
  it('creates no postgame snapshot before a result exists', () => {
    expect(buildRecordSnapshots(
      { wins: 8, losses: 2, ties: 0 },
      'regular_season',
    )).toEqual({
      pregame: {
        record: { wins: 8, losses: 2, ties: 0 },
        scope: 'regular_season',
      },
    });
  });

  it('builds cumulative postseason pregame and postgame records', () => {
    const snapshots = buildPostseasonRecordSnapshots(
      { wins: 12, losses: 5, ties: 0 },
      ['win'],
      'loss',
    );

    expect(snapshots).toEqual({
      pregame: {
        record: { wins: 13, losses: 5, ties: 0 },
        scope: 'season_to_date',
      },
      postgame: {
        record: { wins: 13, losses: 6, ties: 0 },
        scope: 'season_to_date',
      },
    });
  });
});

describe('selectRegularSeasonTeamRecord', () => {
  it('selects the complete regular-season record during the postseason', () => {
    const records = [
      { summary: '13-6' },
      { summary: '12-5' },
    ];

    expect(selectRegularSeasonTeamRecord(records)).toEqual({
      wins: 12,
      losses: 5,
      ties: 0,
    });
  });

  it('returns null when ESPN supplies none', () => {
    expect(selectRegularSeasonTeamRecord(undefined)).toBeNull();
    expect(selectRegularSeasonTeamRecord([])).toBeNull();
  });
});

describe('source record preparation', () => {
  it('selects the first parseable current record', () => {
    expect(selectCurrentTeamRecord([
      { summary: 'unknown' },
      { summary: '13-6' },
      { summary: '12-5' },
    ])).toEqual({ wins: 13, losses: 6, ties: 0 });
  });

  it('derives the results added to a cumulative record', () => {
    expect(deriveAddedResults(
      { wins: 12, losses: 5, ties: 0 },
      { wins: 13, losses: 6, ties: 1 },
    )).toEqual(['win', 'loss', 'tie']);
  });

  it('rejects a cumulative record that predates its base', () => {
    expect(deriveAddedResults(
      { wins: 12, losses: 5, ties: 0 },
      { wins: 11, losses: 6, ties: 0 },
    )).toBeNull();
  });

  it('reverts a result without mutating the source record', () => {
    const record = { wins: 9, losses: 2, ties: 0 };

    expect(revertGameResult(record, 'win')).toEqual({
      wins: 8,
      losses: 2,
      ties: 0,
    });
    expect(record).toEqual({ wins: 9, losses: 2, ties: 0 });
  });

  it('adjusts only the pregame snapshot for a future week', () => {
    const snapshots = buildRecordSnapshots(
      { wins: 9, losses: 2, ties: 0 },
      'regular_season',
      'win',
    );

    expect(revertPregameSnapshot(snapshots, 'win')).toEqual({
      pregame: {
        record: { wins: 8, losses: 2, ties: 0 },
        scope: 'regular_season',
      },
      postgame: {
        record: { wins: 10, losses: 2, ties: 0 },
        scope: 'regular_season',
      },
    });
  });
});
