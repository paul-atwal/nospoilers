import { describe, expect, it } from 'vitest';
import {
  fromEspnWeek,
  toEspnWeek,
} from '../../services/espnWeekMapper';
import {
  getCurrentNflSeason,
  getNextSeasonWeek,
  getPreviousSeasonWeek,
  getRankingWeeksThrough,
  getWeekInfo,
} from '../../utils/scheduleWeek';


describe('ESPN season-week mapping', () => {
  it('preserves the season and maps every supported phase', () => {
    expect(fromEspnWeek(2026, 1, 3)).toEqual({
      season: 2026,
      phase: 'preseason',
      week: 3,
    });
    expect(fromEspnWeek(2026, 2, 18)).toEqual({
      season: 2026,
      phase: 'regular_season',
      week: 18,
    });
    expect(fromEspnWeek(2026, 3, 1)).toEqual({
      season: 2026,
      phase: 'postseason',
      week: 1,
    });
  });

  it('rejects an unsupported ESPN season type', () => {
    expect(fromEspnWeek(2026, 99, 7)).toBeNull();
  });

  it('maps a structured week back to one ESPN query', () => {
    expect(toEspnWeek({
      season: 2026,
      phase: 'postseason',
      week: 5,
    })).toEqual({ season: 2026, seasonType: 3, week: 5 });
  });
});

describe('season-week display', () => {
  it('prepares phase, week, and season labels in one place', () => {
    expect(getWeekInfo({
      season: 2026,
      phase: 'postseason',
      week: 1,
    })).toEqual({
      seasonWeek: {
        season: 2026,
        phase: 'postseason',
        week: 1,
      },
      title: 'Postseason',
      label: 'Wild Card',
      seasonLabel: '2026-27',
    });
  });

  it('keeps source-defined postseason weeks representable', () => {
    expect(getWeekInfo({
      season: 2026,
      phase: 'postseason',
      week: 6,
    }).label).toBe('Postseason Week 6');
  });
});

describe('season-week navigation', () => {
  it('crosses phase boundaries without flattening week numbers', () => {
    expect(getNextSeasonWeek({
      season: 2026,
      phase: 'preseason',
      week: 4,
    })).toEqual({ season: 2026, phase: 'regular_season', week: 1 });
    expect(getNextSeasonWeek({
      season: 2026,
      phase: 'regular_season',
      week: 18,
    })).toEqual({ season: 2026, phase: 'postseason', week: 1 });
    expect(getPreviousSeasonWeek({
      season: 2026,
      phase: 'postseason',
      week: 1,
    })).toEqual({ season: 2026, phase: 'regular_season', week: 18 });
  });

  it('rolls from one season to the next and back', () => {
    expect(getNextSeasonWeek({
      season: 2026,
      phase: 'postseason',
      week: 5,
    })).toEqual({ season: 2027, phase: 'preseason', week: 1 });
    expect(getPreviousSeasonWeek({
      season: 2027,
      phase: 'preseason',
      week: 1,
    })).toEqual({ season: 2026, phase: 'postseason', week: 5 });
  });

  it('builds ranking weeks without including preseason', () => {
    const weeks = getRankingWeeksThrough({
      season: 2026,
      phase: 'postseason',
      week: 2,
    });

    expect(weeks).toHaveLength(20);
    expect(weeks[0]).toEqual({
      season: 2026,
      phase: 'regular_season',
      week: 1,
    });
    expect(weeks.at(-1)).toEqual({
      season: 2026,
      phase: 'postseason',
      week: 2,
    });
  });

  it('derives the NFL season across the calendar-year boundary', () => {
    expect(getCurrentNflSeason(new Date(2027, 0, 15))).toBe(2026);
    expect(getCurrentNflSeason(new Date(2027, 7, 1))).toBe(2027);
  });
});
