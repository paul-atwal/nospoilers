export interface EspnWeek {
  seasonType: number;
  week: number;
}

export const toEspnWeek = (scheduleWeek: number): EspnWeek => {
  if (scheduleWeek <= 18) return { seasonType: 2, week: scheduleWeek };
  return { seasonType: 3, week: scheduleWeek - 18 };
};

export const toScheduleWeek = (week: number, seasonType: number): number => (
  seasonType === 3 ? week + 18 : week
);

export const getWeekLabel = (week: number, seasonType: number): string => {
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
