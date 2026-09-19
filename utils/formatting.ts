export const RATING_BANDS = [
  {
    minScore: 8.5,
    rangeLabel: '8.5+',
    label: 'MUST WATCH',
    guideLabel: 'Must Watch',
    textClass: 'text-purple-400',
    cardColorClass: 'text-purple-400 border-purple-400/50 bg-gradient-to-br from-purple-500/20 to-transparent',
  },
  {
    minScore: 7.0,
    rangeLabel: '7.0+',
    label: 'THRILLER',
    guideLabel: 'Thriller',
    textClass: 'text-green-400',
    cardColorClass: 'text-green-400 border-green-400/50 bg-gradient-to-br from-green-500/20 to-transparent',
  },
  {
    minScore: 5.0,
    rangeLabel: '5.0+',
    label: 'GOOD GAME',
    guideLabel: 'Good Game',
    textClass: 'text-blue-400',
    cardColorClass: 'text-blue-400 border-blue-400/50 bg-gradient-to-br from-blue-500/10 to-transparent',
  },
  {
    minScore: 3.0,
    rangeLabel: '3.0+',
    label: 'DECENT',
    guideLabel: 'Decent',
    textClass: 'text-yellow-400',
    cardColorClass: 'text-yellow-400 border-yellow-400/50 bg-gradient-to-br from-yellow-500/10 to-transparent',
  },
  {
    minScore: Number.NEGATIVE_INFINITY,
    rangeLabel: '<3.0',
    label: 'SKIP IT',
    guideLabel: 'Skip It',
    textClass: 'text-red-400',
    cardColorClass: 'text-red-400 border-red-400/50 bg-gradient-to-br from-red-500/10 to-transparent',
  },
] as const;

export type RatingBand = typeof RATING_BANDS[number];

export const getRatingBand = (score: number): RatingBand => (
  RATING_BANDS.find((band) => score >= band.minScore) ?? RATING_BANDS[RATING_BANDS.length - 1]
);

export const getExcitementColor = (score: number) => getRatingBand(score).cardColorClass;

export const formatDateDisplay = (date: Date) => {
    return new Intl.DateTimeFormat('en-US', { 
        weekday: 'long', 
        month: 'short', 
        day: 'numeric' 
    }).format(date);
};
