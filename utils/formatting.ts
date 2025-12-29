
export const getExcitementColor = (score: number) => {
  if (score >= 9.0) return 'text-purple-400 border-purple-400/50 bg-gradient-to-br from-purple-500/20 to-transparent'; // Epic (Top 10%)
  if (score >= 7.5) return 'text-green-400 border-green-400/50 bg-gradient-to-br from-green-500/20 to-transparent'; // Great (Top 30%)
  if (score >= 6.0) return 'text-blue-400 border-blue-400/50 bg-gradient-to-br from-blue-500/10 to-transparent'; // Good (Above Average)
  if (score >= 4.0) return 'text-yellow-400 border-yellow-400/50 bg-gradient-to-br from-yellow-500/10 to-transparent'; // Okay
  return 'text-gray-400 border-gray-600 bg-gray-800'; // Boring
};

export const getScoreLabel = (score: number) => {
  if (score >= 9.0) return 'MUST WATCH';
  if (score >= 7.5) return 'THRILLER';
  if (score >= 6.0) return 'GOOD GAME';
  if (score >= 4.0) return 'DECENT';
  return 'SKIP IT';
};

export const formatDateDisplay = (date: Date) => {
    return new Intl.DateTimeFormat('en-US', { 
        weekday: 'long', 
        month: 'short', 
        day: 'numeric' 
    }).format(date);
};
