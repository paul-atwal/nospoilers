import type { Game } from '../types';

const BACKEND_API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

export interface ExcitementResult {
  isEstimated: boolean;
  score: number | null;
}

export const fetchGameExcitement = async (game: Game): Promise<ExcitementResult> => {
  if (game.isUpcoming || game.isLive) {
    return { score: null, isEstimated: false };
  }

  try {
    const response = await fetch(`${BACKEND_API_BASE}/excitement/${game.id}`, {
      cache: 'no-store',
    });
    if (!response.ok) return { score: -1, isEstimated: false };

    const data = await response.json();
    return { score: data.excitement_score, isEstimated: false };
  } catch (error) {
    console.error(`Error fetching excitement for ${game.id}`, error);
    return { score: -1, isEstimated: false };
  }
};
