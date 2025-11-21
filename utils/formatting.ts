export const getExcitementColor = (score: number) => {
  if (score >= 8.5) return 'text-purple-400 border-purple-400/50 bg-purple-400/10'; // Epic
  if (score >= 7.0) return 'text-green-400 border-green-400/50 bg-green-400/10'; // Great
  if (score >= 5.0) return 'text-yellow-400 border-yellow-400/50 bg-yellow-400/10'; // Okay
  return 'text-gray-400 border-gray-600 bg-gray-800'; // Boring
};

export const getScoreLabel = (score: number) => {
  if (score >= 9.0) return 'MUST WATCH';
  if (score >= 8.0) return 'THRILLER';
  if (score >= 7.0) return 'GOOD GAME';
  if (score >= 5.0) return 'DECENT';
  return 'SKIP IT';
};

export const formatDateDisplay = (date: Date) => {
    return new Intl.DateTimeFormat('en-US', { 
        weekday: 'long', 
        month: 'short', 
        day: 'numeric' 
    }).format(date);
};
