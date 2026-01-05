
import React, { useState, useEffect, useRef } from 'react';
import Header from './components/Header';
import GameCard from './components/GameCard';
import { Game, WeekInfo } from './types';
import { fetchSchedule, fetchGameExcitement, fetchCurrentWeek } from './services/gemini';
import { Loader2, AlertCircle, Info } from 'lucide-react';

const App: React.FC = () => {
  const [currentWeek, setCurrentWeek] = useState<WeekInfo | null>(null);
  const [games, setGames] = useState<Game[]>([]);
  
  // Separate state for season top games to avoid complexity
  const [seasonTopGames, setSeasonTopGames] = useState<Game[]>([]);
  
  const [loadingSchedule, setLoadingSchedule] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [showRatingInfo, setShowRatingInfo] = useState(false);
  const [viewMode, setViewMode] = useState<'weekly' | 'season'>('weekly');

  // Queue management ref
  const processingQueue = useRef<boolean>(false);

  // Initial load of current NFL week
  useEffect(() => {
    const init = async () => {
      const weekInfo = await fetchCurrentWeek();
      setCurrentWeek(weekInfo);
    };
    init();
  }, []);

  // 1. Fetch Basic Schedule (Fast)
  useEffect(() => {
    if (!currentWeek) return;
    if (viewMode === 'season') return; 

    const loadSchedule = async () => {
      setLoadingSchedule(true);
      setError(null);
      setGames([]); // Clear prev games
      try {
        const fetchedGames = await fetchSchedule(currentWeek.week);
        
        // Initial sort by status
        const sortedGames = fetchedGames.sort((a, b) => {
          if (a.isLive && !b.isLive) return -1;
          if (!a.isLive && b.isLive) return 1;
          if (!a.isUpcoming && b.isUpcoming) return -1;
          if (a.isUpcoming && !b.isUpcoming) return 1;
          return 0;
        });

        setGames(sortedGames);
      } catch (err) {
        console.error(err);
        setError("Failed to load schedule.");
      } finally {
        setLoadingSchedule(false);
      }
    };

    loadSchedule();
  }, [currentWeek, viewMode]);

  // 2. Progressive Loading Queue for Weekly View
  // 2. Progressive Loading Queue for Weekly View
  useEffect(() => {
      if (viewMode === 'season') return;
      
      let isMounted = true;

      const processNextGame = async () => {
          // Find next game that needs a score (score is null, not upcoming, and NOT live)
          const gameIndex = games.findIndex(g => g.excitementScore === null && !g.isUpcoming && !g.isLive);
          
          if (gameIndex === -1) return; // All done

          const gameToProcess = games[gameIndex];

          try {
              // Add a small delay to be nice to the API
              await new Promise(r => setTimeout(r, 100));
              if (!isMounted) return;
              
              const result = await fetchGameExcitement(gameToProcess);
              if (!isMounted) return;
              
              setGames(prevGames => {
                  const newGames = [...prevGames];
                  const idx = newGames.findIndex(g => g.id === gameToProcess.id);
                  if (idx !== -1) {
                      newGames[idx] = {
                          ...newGames[idx],
                          excitementScore: result.score,
                          isEstimated: result.isEstimated
                      };
                  }
                  return newGames;
              });
          } catch (e) {
              console.error("Queue error", e);
          }
      };

      processNextGame();

      return () => {
          isMounted = false;
      };
  }, [games, viewMode]);


  // 3. Special Handler for Season Mode (Bulk fetch then sort)
  useEffect(() => {
      if (viewMode === 'season' && currentWeek) {
          const loadSeasonBest = async () => {
              setLoadingSchedule(true);
              setError(null);
              try {
                  if (seasonTopGames.length > 0) {
                      setLoadingSchedule(false);
                      return;
                  }

                  // Fetch all weeks of current season
                  const maxWeek = currentWeek.week;
                  const startWeek = 1;
                  
                  const schedulePromises = [];
                  for(let i = startWeek; i <= maxWeek; i++) {
                      schedulePromises.push(fetchSchedule(i));
                  }
                  
                  const weeklySchedules = await Promise.all(schedulePromises);
                  const allGames = weeklySchedules.flat().filter(g => !g.isUpcoming);

                  // OPTIMIZED: Fetch all scores in parallel (they're cached!)
                  // This is much faster than sequential fetching
                  const scorePromises = allGames.map(game => 
                      fetchGameExcitement(game).then(({ score }) => ({ ...game, excitementScore: score }))
                  );
                  
                  const processedGames = await Promise.all(scorePromises);

                  const bestGames = processedGames
                    .sort((a, b) => (b.excitementScore || 0) - (a.excitementScore || 0))
                    .slice(0, 10);

                  setSeasonTopGames(bestGames);
              } catch (e) {
                  console.error(e);
                  setError("Could not load season data.");
              } finally {
                  setLoadingSchedule(false);
              }
          };
          loadSeasonBest();
      }
  }, [viewMode, currentWeek]);

  const handleWeekChange = (newContinuousWeek: number) => {
    if (!currentWeek) return;
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
            <h3 className="font-bold text-white mb-2">How Games Are Rated</h3>
            <p className="text-xs opacity-80 mb-3">
              Ratings are calculated using play-by-play data from 2,600+ games (2016-2025). The average game scores around 5.0.
            </p>
            <div className="space-y-2 text-xs opacity-80 mb-3">
              <div>
                <span className="text-blue-400 font-medium">Game Volatility (Primary):</span> Measures dramatic swings in win probability throughout the game. High volatility = back-and-forth action, late-game heroics, and sustained tension.
              </div>
              <div>
                <span className="text-purple-400 font-medium">Comeback Factor (Bonus):</span> Rewards teams that overcome significant deficits, adding narrative drama beyond raw volatility.
              </div>
            </div>
            <div className="border-t border-white/10 pt-3">
              <p className="text-xs font-medium text-white mb-2">Score Guide</p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
                <div><span className="text-purple-400">9.0+</span> <span className="opacity-70">— Must Watch (Top 5%)</span></div>
                <div><span className="text-green-400">7.5+</span> <span className="opacity-70">— Thriller (Top 15%)</span></div>
                <div><span className="text-blue-400">6.0+</span> <span className="opacity-70">— Good Game</span></div>
                <div><span className="text-yellow-400">4.0+</span> <span className="opacity-70">— Decent</span></div>
                <div><span className="text-red-400">&lt;4.0</span> <span className="opacity-70">— Skip It</span></div>
              </div>
            </div>
          </div>
        )}

        {loadingSchedule && (
          <div className="flex flex-col items-center justify-center py-24 gap-4 opacity-60">
            <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
            <p className="text-xs tracking-widest uppercase">
                {viewMode === 'season' ? "Analyzing Season Data..." : `Loading ${currentWeek.label}...`}
            </p>
          </div>
        )}

        {!loadingSchedule && error && (
          <div className="bg-red-900/10 border border-red-900/50 rounded-xl p-6 text-center">
            <AlertCircle className="w-8 h-8 text-red-500 mx-auto mb-2" />
            <p className="text-red-200">{error}</p>
          </div>
        )}

        {!loadingSchedule && !error && (
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
