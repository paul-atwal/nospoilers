import { describe, expect, it } from 'vitest';
import { getExcitementColor, getRatingBand, RATING_BANDS } from '../../utils/formatting';

describe('rating band presentation', () => {
  it.each([
    [8.4, '7.0+', 'THRILLER', 'text-green-400'],
    [8.5, '8.5+', 'MUST WATCH', 'text-purple-400'],
    [6.99, '5.0+', 'GOOD GAME', 'text-blue-400'],
    [7.0, '7.0+', 'THRILLER', 'text-green-400'],
    [4.99, '3.0+', 'DECENT', 'text-yellow-400'],
    [5.0, '5.0+', 'GOOD GAME', 'text-blue-400'],
    [2.99, '<3.0', 'SKIP IT', 'text-red-400'],
    [3.0, '3.0+', 'DECENT', 'text-yellow-400'],
  ])('classifies %s using the same band as the guide', (score, rangeLabel, label, textClass) => {
    const band = getRatingBand(score);
    expect(band.rangeLabel).toBe(rangeLabel);
    expect(band.label).toBe(label);
    expect(getExcitementColor(score)).toContain(textClass);
  });

  it('keeps every guide row backed by a card color and score label', () => {
    for (const band of RATING_BANDS) {
      const representative = band.minScore === Number.NEGATIVE_INFINITY ? 0 : band.minScore;
      expect(getRatingBand(representative)).toBe(band);
      expect(band.cardColorClass).toContain(band.textClass);
    }
  });
});
