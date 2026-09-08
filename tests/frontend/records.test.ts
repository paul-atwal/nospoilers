import { describe, expect, it } from 'vitest';
import { formatTeamRecord } from '../../utils/records';

describe('record display formatting', () => {
  it('formats supplied records without deriving results', () => {
    expect(formatTeamRecord({ wins: 12, losses: 5, ties: 0 })).toBe('12-5');
    expect(formatTeamRecord({ wins: 8, losses: 2, ties: 1 })).toBe('8-2-1');
  });
});
