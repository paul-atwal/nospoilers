import { describe, expect, it } from 'vitest';
import {
  applyGameResult,
  buildRecordSnapshots,
  formatTeamRecord,
  parseTeamRecord,
  revertGameResult,
  revertPregameSnapshot,
  selectOverallTeamRecord,
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

});

describe('selectOverallTeamRecord', () => {
  it('selects ESPN overall/total data instead of home or road splits', () => {
    const records = [
      { name: 'Home', type: 'home', summary: '5-3' },
      { name: 'Road', type: 'road', summary: '5-4' },
      { name: 'overall', type: 'total', summary: '10-7' },
    ];

    expect(selectOverallTeamRecord(records)).toEqual({
      wins: 10,
      losses: 7,
      ties: 0,
    });
  });

  it('returns null when ESPN supplies none', () => {
    expect(selectOverallTeamRecord(undefined)).toBeNull();
    expect(selectOverallTeamRecord([])).toBeNull();
  });
});

describe('source record preparation', () => {
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
