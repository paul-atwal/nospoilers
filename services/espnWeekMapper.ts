import type { SeasonPhase, SeasonWeek } from '../types';


export type EspnSeasonType = 1 | 2 | 3;

export interface EspnWeek {
  season: number;
  seasonType: EspnSeasonType;
  week: number;
}

const ESPN_PHASES: Readonly<Record<EspnSeasonType, SeasonPhase>> = {
  1: 'preseason',
  2: 'regular_season',
  3: 'postseason',
};

const ESPN_SEASON_TYPES: Readonly<Record<SeasonPhase, EspnSeasonType>> = {
  preseason: 1,
  regular_season: 2,
  postseason: 3,
};

export const fromEspnWeek = (
  season: number,
  seasonType: number,
  week: number,
): SeasonWeek | null => {
  if (
    !Number.isInteger(season)
    || season < 1
    || !Number.isInteger(week)
    || week < 1
    || (seasonType !== 1 && seasonType !== 2 && seasonType !== 3)
  ) {
    return null;
  }

  return {
    season,
    phase: ESPN_PHASES[seasonType],
    week,
  };
};

export const toEspnWeek = (seasonWeek: SeasonWeek): EspnWeek => ({
  season: seasonWeek.season,
  seasonType: ESPN_SEASON_TYPES[seasonWeek.phase],
  week: seasonWeek.week,
});
