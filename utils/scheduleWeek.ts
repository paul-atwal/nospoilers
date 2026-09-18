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

export const isProBowlWeek = (seasonWeek: SeasonWeek): boolean => (
  seasonWeek.phase === 'postseason' && seasonWeek.week === 4
);

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
  knownWeeks?: readonly SeasonWeek[],
): SeasonWeek => {
  if (knownWeeks) {
    const index = knownWeeks.findIndex((candidate) => sameSeasonWeek(candidate, seasonWeek));
    if (index > 0) return knownWeeks[index - 1];
    return seasonWeek;
  }
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
  knownWeeks?: readonly SeasonWeek[],
): SeasonWeek => {
  if (knownWeeks) {
    const index = knownWeeks.findIndex((candidate) => sameSeasonWeek(candidate, seasonWeek));
    if (index >= 0 && index < knownWeeks.length - 1) return knownWeeks[index + 1];
    return seasonWeek;
  }
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

export const getAvailableSeasons = (
  knownWeeks: readonly SeasonWeek[],
  activeSeason?: number,
): number[] => Array.from(new Set([
  ...knownWeeks.map((seasonWeek) => seasonWeek.season),
  ...(activeSeason === undefined ? [] : [activeSeason]),
])).sort((left, right) => right - left);

export const getVisibleSeasonWeeks = (
  knownWeeks: readonly SeasonWeek[],
  season: number,
  activeSeason: number,
  currentWeek: SeasonWeek,
): SeasonWeek[] => {
  const showPreseason = season === activeSeason
    && currentWeek.season === season
    && currentWeek.phase === 'preseason';

  return knownWeeks.filter((candidate) => (
    candidate.season === season
      && !isProBowlWeek(candidate)
      && (showPreseason || candidate.phase !== 'preseason')
  ));
};

const getCircularAdjacentSeasonWeek = (
  seasonWeek: SeasonWeek,
  visibleWeeks: readonly SeasonWeek[],
  direction: -1 | 1,
): SeasonWeek => {
  if (visibleWeeks.length === 0) return seasonWeek;
  const index = visibleWeeks.findIndex((candidate) => sameSeasonWeek(candidate, seasonWeek));
  if (index < 0) return visibleWeeks[direction < 0 ? visibleWeeks.length - 1 : 0];
  const nextIndex = (index + direction + visibleWeeks.length) % visibleWeeks.length;
  return visibleWeeks[nextIndex];
};

export const getPreviousVisibleSeasonWeek = (
  seasonWeek: SeasonWeek,
  visibleWeeks: readonly SeasonWeek[],
): SeasonWeek => getCircularAdjacentSeasonWeek(seasonWeek, visibleWeeks, -1);

export const getNextVisibleSeasonWeek = (
  seasonWeek: SeasonWeek,
  visibleWeeks: readonly SeasonWeek[],
): SeasonWeek => getCircularAdjacentSeasonWeek(seasonWeek, visibleWeeks, 1);

export const sameSeasonWeek = (left: SeasonWeek, right: SeasonWeek): boolean => (
  left.season === right.season
    && left.phase === right.phase
    && left.week === right.week
);

export const selectWeekAfterBootstrapRefresh = (
  previousSelection: SeasonWeek | null,
  previousSourceCurrentWeek: SeasonWeek | null,
  refreshedSourceCurrentWeek: SeasonWeek,
  selectionWasDeliberate: boolean,
): SeasonWeek => {
  if (previousSelection === null) return refreshedSourceCurrentWeek;
  if (!selectionWasDeliberate && previousSourceCurrentWeek !== null && sameSeasonWeek(previousSelection, previousSourceCurrentWeek)) {
    return refreshedSourceCurrentWeek;
  }
  return previousSelection;
};

export const isFirstKnownWeek = (
  seasonWeek: SeasonWeek,
  knownWeeks: readonly SeasonWeek[],
): boolean => knownWeeks.length > 0 && sameSeasonWeek(knownWeeks[0], seasonWeek);

export const isLastKnownWeek = (
  seasonWeek: SeasonWeek,
  knownWeeks: readonly SeasonWeek[],
): boolean => knownWeeks.length > 0 && sameSeasonWeek(knownWeeks[knownWeeks.length - 1], seasonWeek);

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
