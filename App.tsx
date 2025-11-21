
import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import GameCard from './components/GameCard';
import { Game, WeekInfo } from './types';
import { fetchGamesForWeek, fetchCurrentWeek } from './services/gemini';
import { Loader2, AlertCircle, Info } from 'lucide-react';

const App: React.FC = () => {
  const [currentWeek, setCurrentWeek] = useState<WeekInfo | null>(null);
  const [games, setGames] = useState<Game[]>([]);
  const [seasonTopGames, setSeasonTopGames] = useState<Game[]>([]);
  
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [showRatingInfo, setShowRatingInfo] = useState(false);
  
  const [viewMode, setViewMode] = useState<'weekly' | 'season'>('weekly');

  // Initial load of current NFL week
  useEffect(() => {
    const init = async () => {
      const weekInfo = await fetchCurrentWeek();
      setCurrentWeek(weekInfo);
    };
    init();
  }, []);

  // Fetch games when week changes or view mode switches to weekly
  useEffect(() => {
    if (!currentWeek) return;
    if (viewMode === 'season') return; // Don't refetch weekly data if in season mode

    const loadGames = async () => {
      setLoading(true);
      setError(null);
      try {
        const fetchedGames = await fetchGamesForWeek(currentWeek.week);
        
        const sortedGames = fetchedGames.sort((a, b) => {
          const getStatusPriority = (g: Game) => {
            if (g.isLive) return 0;
            if (!g.isUpcoming) return 1; // Finished
            return 2; // Upcoming
          };

          const priorityA = getStatusPriority(a);
          const priorityB = getStatusPriority(b);

          if (priorityA !== priorityB) return priorityA - priorityB;

          if (!a.isUpcoming && !b.isUpcoming) {
            return b.excitementScore - a.excitementScore;
          }

          return 0;
        });

        setGames(sortedGames);
      } catch (err) {
        console.error(err);
        setError("Failed to load game data.");
      } finally {
        setLoading(false);
      }
    };

    loadGames();
  }, [currentWeek, viewMode]);

  // Handle "Best of Season" fetch
  useEffect(() => {
      if (viewMode === 'season' && currentWeek) {
          const loadSeasonBest = async () => {
              setLoading(true);
              setError(null);
              try {
                  if (seasonTopGames.length > 0) {
                      setLoading(false);
                      return;
                  }

                  const maxWeek = currentWeek.week;
                  const weeksToFetch = [];
                  // Fetch all weeks up to current week
                  for(let i = 1; i <= maxWeek; i++) {
                      weeksToFetch.push(fetchGamesForWeek(i));
                  }
                  
                  const results = await Promise.all(weeksToFetch);
                  const allGames = results.flat();
                  
                  const bestGames = allGames
                    .filter(g => !g.isUpcoming && g.status === 'Final')
                    .sort((a, b) => b.excitementScore - a.excitementScore)
                    .slice(0, 10); // Top 10

                  setSeasonTopGames(bestGames);
              } catch (e) {
                  console.error(e);
                  setError("Could not load season data.");
              } finally {
                  setLoading(false);
              }
          };
          loadSeasonBest();
      }
  }, [viewMode, currentWeek]);

  // Helper to change week (updates state)
  const handleWeekChange = (newContinuousWeek: number) => {
    if (!currentWeek) return;
    // We don't need to calculate seasonType here, fetchGamesForWeek handles it based on the number
    // We just need to update the label for the state object
    // fetchGamesForWeek recalculates labels, so we mainly need the number.
    setCurrentWeek({ 
        ...currentWeek, 
        week: newContinuousWeek, 
        seasonType: newContinuousWeek > 18 ? 3 : 2 
    });
  };

  if (!currentWeek) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[#121212] text-white">
         <Loader2 className="w-8 h-8 text-blue-500 animate-spin mb-4" />
         <p className="text-xs tracking-widest uppercase text-neutral-500">Loading NFL Season...</p>
      </div>
    );
  }

  const displayedGames = viewMode === 'season' ? seasonTopGames : games;

  return (
    <div className="min-h-screen flex flex-col bg-[#121212] text-white font-sans">
      <Header 
        currentWeek={currentWeek} 
        onWeekChange={handleWeekChange} 
        viewMode={viewMode}
        onViewModeChange={setViewMode}
      />

      <main className="flex-1 max-w-2xl mx-auto w-full px-4 py-6 relative">
        
        {/* Info Banner / Tooltip Toggle - Aligned with content */}
        <div className="flex justify-between items-end mb-4 px-1">
            <div>
              {viewMode === 'weekly' ? (
                 <h2 className="text-lg font-bold text-white">Game Rankings</h2>
              ) : (
                 <h2 className="text-lg font-bold text-yellow-400">Season Leaders</h2>
              )}
            </div>
            <button 
              onClick={() => setShowRatingInfo(!showRatingInfo)}
              className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider font-bold text-neutral-500 hover:text-blue-400 transition-colors"
            >
              <Info className="w-3 h-3" />
              Rating Info
            </button>
        </div>

        {showRatingInfo && (
          <div className="mb-6 bg-neutral-800/50 border border-white/10 rounded-xl p-4 text-sm text-neutral-300 animate-in fade-in slide-in-from-top-2">
            <h3 className="font-bold text-white mb-2">The Excitement Formula</h3>
            <ul className="list-disc list-inside space-y-1 text-xs opacity-80">
              <li><span className="text-blue-400 font-medium">Close Scores:</span> Games within one possession score highest.</li>
              <li><span className="text-blue-400 font-medium">Comebacks:</span> Big swings in win probability add bonuses.</li>
              <li><span className="text-blue-400 font-medium">High Stakes:</span> Overtime games are automatic thrillers.</li>
            </ul>
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div className="flex flex-col items-center justify-center py-24 gap-4 opacity-60">
            <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
            <p className="text-xs tracking-widest uppercase">
                {viewMode === 'season' ? "Analyzing Season Data..." : `Loading ${currentWeek.label}...`}
            </p>
          </div>
        )}

        {/* Error */}
        {!loading && error && (
          <div className="bg-red-900/10 border border-red-900/50 rounded-xl p-6 text-center">
            <AlertCircle className="w-8 h-8 text-red-500 mx-auto mb-2" />
            <p className="text-red-200">{error}</p>
          </div>
        )}

        {/* Game List */}
        {!loading && !error && (
          <div className="flex flex-col gap-3">
            {displayedGames.length === 0 ? (
               <div className="text-center py-20 text-neutral-500 text-sm">
                  {viewMode === 'season' ? "Not enough data for season rankings yet." : "No games found."}
               </div>
            ) : (
               displayedGames.map((game) => (
                 <GameCard 
                    key={game.id} 
                    game={game} 
                    showWeekContext={viewMode === 'season'}
                 />
               ))
            )}
          </div>
        )}
      </main>
    </div>
  );
};

export default App;
