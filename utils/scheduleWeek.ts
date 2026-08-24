import type { SupportedSeasonType, WeekInfo } from '../types';

export interface EspnWeek {
  seasonType: SupportedSeasonType;
  week: number;
}

export const toEspnWeek = (scheduleWeek: number): EspnWeek => {
  if (scheduleWeek <= 18) return { seasonType: 2, week: scheduleWeek };
  return { seasonType: 3, week: scheduleWeek - 18 };
};

export const toScheduleWeek = (week: number, seasonType: SupportedSeasonType): number => (
  seasonType === 3 ? week + 18 : week
);

export const toSupportedWeekInfo = (
  week: number,
  seasonType: number,
): WeekInfo => {
  if (seasonType !== 2 && seasonType !== 3) {
    return { scheduleWeek: 1, seasonType: 2, label: 'Week 1' };
  }

  return {
    scheduleWeek: toScheduleWeek(week, seasonType),
    seasonType,
    label: getWeekLabel(week, seasonType),
  };
};

export const getWeekLabel = (week: number, seasonType: SupportedSeasonType): string => {
  if (seasonType !== 3) return `Week ${week}`;

  const postseasonLabels: Record<number, string> = {
    1: 'Wild Card',
    2: 'Divisional Round',
    3: 'Championship Round',
    4: 'Pro Bowl',
    5: 'Super Bowl',
  };

  return postseasonLabels[week] ?? 'Postseason';
};
