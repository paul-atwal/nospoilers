import { describe, expect, it } from 'vitest';
import {
  getWeekLabel,
  toEspnWeek,
  toScheduleWeek,
} from '../../utils/scheduleWeek';

describe('schedule week conversion', () => {
  it('keeps regular-season week numbers unchanged', () => {
    expect(toEspnWeek(18)).toEqual({ seasonType: 2, week: 18 });
  });

  it('maps postseason schedule weeks to ESPN week numbers', () => {
    expect(toEspnWeek(19)).toEqual({ seasonType: 3, week: 1 });
    expect(toEspnWeek(23)).toEqual({ seasonType: 3, week: 5 });
    expect(toScheduleWeek(1, 3)).toBe(19);
  });

  it('returns the existing postseason labels', () => {
    expect(getWeekLabel(1, 3)).toBe('Wild Card');
    expect(getWeekLabel(5, 3)).toBe('Super Bowl');
  });
});
