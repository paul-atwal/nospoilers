import type { SeasonPhase, SeasonWeek, WeekInfo } from '../types';


const PHASE_WEEK_COUNTS: Readonly<Record<SeasonPhase, number>> = {
  preseason: 4,
  regular_season: 18,
  postseason: 5,
};

const POSTSEASON_LABELS: Readonly<Record<number, string>> = {
  1: 'Wild Card',
  2: 'Divisional Round',
  3: 'Championship',
  4: 'Pro Bowl',
  5: 'Super Bowl',
};

const PHASE_TITLES: Readonly<Record<SeasonPhase, string>> = {
  preseason: 'Preseason',
  regular_season: 'Regular Season',
  postseason: 'Postseason',
};

const makeSeasonWeek = (
  season: number,
  phase: SeasonPhase,
  week: number,
): SeasonWeek => ({ season, phase, week });

export const getCurrentNflSeason = (now: Date = new Date()): number => (
  now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1
);

export const getWeekInfo = (seasonWeek: SeasonWeek): WeekInfo => {
  const { season, phase, week } = seasonWeek;
  const label = phase === 'postseason'
    ? POSTSEASON_LABELS[week] ?? `Postseason Week ${week}`
    : `Week ${week}`;

  return {
    seasonWeek,
    title: PHASE_TITLES[phase],
    label,
    seasonLabel: `${season}-${String(season + 1).slice(-2)}`,
  };
};

export const getPreviousSeasonWeek = (
  seasonWeek: SeasonWeek,
): SeasonWeek => {
  const { season, phase, week } = seasonWeek;
  if (week > 1) return makeSeasonWeek(season, phase, week - 1);

  if (phase === 'postseason') {
    return makeSeasonWeek(
      season,
      'regular_season',
      PHASE_WEEK_COUNTS.regular_season,
    );
  }
  if (phase === 'regular_season') {
    return makeSeasonWeek(
      season,
      'preseason',
      PHASE_WEEK_COUNTS.preseason,
    );
  }
  return makeSeasonWeek(
    season - 1,
    'postseason',
    PHASE_WEEK_COUNTS.postseason,
  );
};

export const getNextSeasonWeek = (
  seasonWeek: SeasonWeek,
): SeasonWeek => {
  const { season, phase, week } = seasonWeek;
  if (week < PHASE_WEEK_COUNTS[phase]) {
    return makeSeasonWeek(season, phase, week + 1);
  }

  if (phase === 'preseason') {
    return makeSeasonWeek(season, 'regular_season', 1);
  }
  if (phase === 'regular_season') {
    return makeSeasonWeek(season, 'postseason', 1);
  }
  return makeSeasonWeek(season + 1, 'preseason', 1);
};

export const getRankingWeeksThrough = (
  currentWeek: SeasonWeek,
): SeasonWeek[] => {
  if (currentWeek.phase === 'preseason') return [];

  const regularWeekCount = currentWeek.phase === 'regular_season'
    ? currentWeek.week
    : PHASE_WEEK_COUNTS.regular_season;
  const regularSeasonWeeks = Array.from(
    { length: regularWeekCount },
    (_, index) => makeSeasonWeek(
      currentWeek.season,
      'regular_season',
      index + 1,
    ),
  );

  if (currentWeek.phase === 'regular_season') return regularSeasonWeeks;

  const postseasonWeeks = Array.from(
    { length: currentWeek.week },
    (_, index) => makeSeasonWeek(
      currentWeek.season,
      'postseason',
      index + 1,
    ),
  );
  return [...regularSeasonWeeks, ...postseasonWeeks];
};
