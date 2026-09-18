import { describe, expect, it } from 'vitest';
import {
  getAvailableSeasons,
  getNextVisibleSeasonWeek,
  getCurrentNflSeason,
  getNextSeasonWeek,
  getPreviousVisibleSeasonWeek,
  getPreviousSeasonWeek,
  getRankingWeeksThrough,
  getVisibleSeasonWeeks,
  getWeekInfo,
  isProBowlWeek,
  selectWeekAfterBootstrapRefresh,
} from '../../utils/scheduleWeek';


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

  it('navigates only within the ordered API catalogue at irregular season boundaries', () => {
    const catalogue = [
      { season: 2020, phase: 'preseason' as const, week: 5 },
      { season: 2020, phase: 'regular_season' as const, week: 1 },
      { season: 2020, phase: 'regular_season' as const, week: 17 },
      { season: 2020, phase: 'postseason' as const, week: 5 },
      { season: 2021, phase: 'preseason' as const, week: 1 },
    ];

    expect(getNextSeasonWeek(catalogue[0], catalogue)).toEqual(catalogue[1]);
    expect(getPreviousSeasonWeek(catalogue[0], catalogue)).toEqual(catalogue[0]);
    expect(getNextSeasonWeek(catalogue[3], catalogue)).toEqual(catalogue[4]);
    expect(getPreviousSeasonWeek(catalogue[4], catalogue)).toEqual(catalogue[3]);
    expect(getNextSeasonWeek(catalogue[4], catalogue)).toEqual(catalogue[4]);
  });

  it('follows source current-week rollover only while selection is untouched', () => {
    const oldSource = { season: 2026, phase: 'regular_season' as const, week: 18 };
    const newSource = { season: 2026, phase: 'postseason' as const, week: 1 };
    expect(selectWeekAfterBootstrapRefresh(oldSource, oldSource, newSource, false)).toEqual(newSource);
    expect(selectWeekAfterBootstrapRefresh({ ...oldSource, week: 1 }, oldSource, newSource, true)).toEqual({ ...oldSource, week: 1 });
  });

  it('derives descending seasons and omits historical preseason and Pro Bowl weeks', () => {
    const catalogue = [
      { season: 2024, phase: 'preseason' as const, week: 1 },
      { season: 2024, phase: 'regular_season' as const, week: 1 },
      { season: 2024, phase: 'postseason' as const, week: 4 },
      { season: 2024, phase: 'postseason' as const, week: 5 },
      { season: 2025, phase: 'preseason' as const, week: 1 },
      { season: 2026, phase: 'preseason' as const, week: 1 },
    ];

    expect(getAvailableSeasons(catalogue, 2026)).toEqual([2026, 2025, 2024]);
    expect(getVisibleSeasonWeeks(catalogue, 2024, 2026, catalogue[5])).toEqual([
      catalogue[1],
      catalogue[3],
    ]);
    expect(isProBowlWeek(catalogue[2])).toBe(true);
  });

  it('keeps preseason available only while the active season is in preseason', () => {
    const catalogue = [
      { season: 2026, phase: 'preseason' as const, week: 1 },
      { season: 2026, phase: 'regular_season' as const, week: 1 },
    ];

    expect(getVisibleSeasonWeeks(catalogue, 2026, 2026, catalogue[0])).toEqual(catalogue);
    expect(getVisibleSeasonWeeks(catalogue, 2026, 2026, catalogue[1])).toEqual([catalogue[1]]);
  });

  it('wraps only inside the selected season timeline', () => {
    const timeline = [
      { season: 2024, phase: 'regular_season' as const, week: 1 },
      { season: 2024, phase: 'postseason' as const, week: 5 },
    ];

    expect(getPreviousVisibleSeasonWeek(timeline[0], timeline)).toEqual(timeline[1]);
    expect(getNextVisibleSeasonWeek(timeline[1], timeline)).toEqual(timeline[0]);
  });
});
