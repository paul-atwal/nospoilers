import { describe, expect, it } from 'vitest';
import {
  revertRecord,
  selectRegularSeasonRecord,
} from '../../utils/records';

describe('selectRegularSeasonRecord', () => {
  it('selects the complete regular-season record during the postseason', () => {
    const records = [
      { summary: '13-6' },
      { summary: '12-5' },
    ];

    expect(selectRegularSeasonRecord(records)).toBe('12-5');
  });

  it('returns an empty record when ESPN supplies none', () => {
    expect(selectRegularSeasonRecord(undefined)).toBe('');
    expect(selectRegularSeasonRecord([])).toBe('');
  });
});

describe('revertRecord', () => {
  it('leaves an unrecognized record unchanged', () => {
    expect(revertRecord('unknown', 'win')).toBe('unknown');
  });
});
