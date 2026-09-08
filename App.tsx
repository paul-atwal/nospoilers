import React, { useEffect, useRef, useState } from 'react';
import Header from './components/Header';
import GameCard from './components/GameCard';
import type { ApiGame, BootstrapResponse, SeasonSnapshotResponse, SeasonWeek, WeekSnapshotResponse } from './types';
import { toViewGame } from './services/gameViewModel';
import { ReadApiClient, type ReadApiResponse } from './services/readApi';
import { createRequestOwner, type RequestOwner } from './services/requestLifecycle';
import { AlertCircle, Info, Loader2 } from 'lucide-react';
import { getNextSeasonWeek, getPreviousSeasonWeek, getWeekInfo, isFirstKnownWeek, isLastKnownWeek, selectWeekAfterBootstrapRefresh } from './utils/scheduleWeek';

export { toViewGame } from './services/gameViewModel';

export const selectBestSeasonGames = (games: readonly ApiGame[]): readonly ApiGame[] => games
  .filter((game) => game.seasonWeek.phase !== 'preseason' && game.status.state === 'final')
  .filter((game) => (game.rating.state === 'confirmed' || game.rating.state === 'provisional') && game.rating.score !== null)
  .slice(0, 10);

const weekKey = (week: SeasonWeek | null): string => week ? `${week.season}/${week.phase}/${week.week}` : '';
const getErrorMessage = (error: unknown, fallback: string): string => error instanceof Error && error.message ? error.message : fallback;

const App: React.FC = () => {
  const apiRef = useRef<ReadApiClient | null>(null);
  if (!apiRef.current) apiRef.current = new ReadApiClient();
  const bootstrapOwner = useRef<RequestOwner>(createRequestOwner());
  const weeklyOwner = useRef<RequestOwner>(createRequestOwner());
  const seasonOwner = useRef<RequestOwner>(createRequestOwner());
  const selectedWasChanged = useRef(false);
  const sourceCurrentWeekRef = useRef<SeasonWeek | null>(null);
  const [bootstrap, setBootstrap] = useState<BootstrapResponse | null>(null);
  const [selectedWeek, setSelectedWeek] = useState<SeasonWeek | null>(null);
  const [bootstrapLoading, setBootstrapLoading] = useState(true);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [weeklySnapshots, setWeeklySnapshots] = useState<Record<string, WeekSnapshotResponse>>({});
  const [seasonSnapshots, setSeasonSnapshots] = useState<Record<number, SeasonSnapshotResponse>>({});
  const [weeklyLoading, setWeeklyLoading] = useState(false);
  const [seasonLoading, setSeasonLoading] = useState(false);
  const [weeklyError, setWeeklyError] = useState<string | null>(null);
  const [seasonError, setSeasonError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'weekly' | 'season'>('weekly');
  const [showRatingInfo, setShowRatingInfo] = useState(false);
  const refreshBootstrap = () => bootstrapOwner.current.retry();

  useEffect(() => {
    const owner = bootstrapOwner.current;
    void owner.start({
      load: (signal) => apiRef.current!.fetchBootstrap(signal), getPollAfterSeconds: (response) => response.pollAfterSeconds,
      onLoading: setBootstrapLoading,
      onData: (response: ReadApiResponse<BootstrapResponse>) => {
        const previousSourceCurrentWeek = sourceCurrentWeekRef.current;
        setBootstrap(response.body);
        setSelectedWeek((previous) => selectWeekAfterBootstrapRefresh(
          previous,
          previousSourceCurrentWeek,
          response.body.currentWeek,
          selectedWasChanged.current,
        ));
        sourceCurrentWeekRef.current = response.body.currentWeek;
        setBootstrapError(null);
      },
      onError: (error) => setBootstrapError(getErrorMessage(error, 'Failed to load the NFL week catalogue.')),
    });
    return () => owner.cancel();
  }, []);

  useEffect(() => {
    const owner = weeklyOwner.current;
    if (!bootstrap || !selectedWeek || viewMode !== 'weekly') { owner.cancel(); return; }
    const key = weekKey(selectedWeek);
    const cached = apiRef.current!.getCachedWeekSnapshot(selectedWeek);
    if (cached) setWeeklySnapshots((previous) => ({ ...previous, [key]: cached.body }));
    setWeeklyError(null);
    void owner.start({
      load: (signal) => apiRef.current!.fetchWeekSnapshot(selectedWeek, signal), getPollAfterSeconds: (response) => response.pollAfterSeconds,
      onLoading: setWeeklyLoading,
      onData: (response: ReadApiResponse<WeekSnapshotResponse>) => { setWeeklySnapshots((previous) => ({ ...previous, [key]: response.body })); setWeeklyError(null); },
      onError: (error) => setWeeklyError(getErrorMessage(error, 'Failed to load this week.')),
    });
    return () => owner.cancel();
  }, [Boolean(bootstrap), weekKey(selectedWeek), viewMode]);

  useEffect(() => {
    const owner = seasonOwner.current;
    if (!bootstrap || viewMode !== 'season') { owner.cancel(); return; }
    const season = bootstrap.activeSeason;
    const cached = apiRef.current!.getCachedSeasonSnapshot(season);
    if (cached) setSeasonSnapshots((previous) => ({ ...previous, [season]: cached.body }));
    setSeasonError(null);
    void owner.start({
      load: (signal) => apiRef.current!.fetchSeasonSnapshot(season, signal), getPollAfterSeconds: (response) => response.pollAfterSeconds,
      onLoading: setSeasonLoading,
      onData: (response: ReadApiResponse<SeasonSnapshotResponse>) => { setSeasonSnapshots((previous) => ({ ...previous, [season]: response.body })); setSeasonError(null); },
      onError: (error) => setSeasonError(getErrorMessage(error, 'Could not load season data.')),
    });
    return () => owner.cancel();
  }, [bootstrap?.activeSeason, viewMode]);

  useEffect(() => {
    let queued = false;
    const onReturn = () => {
      if (document.visibilityState === 'hidden' || queued) return;
      queued = true;
      queueMicrotask(() => { queued = false; void bootstrapOwner.current.refresh(); if (viewMode === 'weekly') void weeklyOwner.current.refresh(); else void seasonOwner.current.refresh(); });
    };
    document.addEventListener('visibilitychange', onReturn); window.addEventListener('focus', onReturn);
    return () => { document.removeEventListener('visibilitychange', onReturn); window.removeEventListener('focus', onReturn); };
  }, [viewMode]);

  useEffect(() => () => { bootstrapOwner.current.cancel(); weeklyOwner.current.cancel(); seasonOwner.current.cancel(); }, []);

  const currentWeek = selectedWeek ? getWeekInfo(selectedWeek) : null;
  const sourceCurrentWeek = bootstrap ? getWeekInfo(bootstrap.currentWeek) : null;
  const knownWeeks = bootstrap?.knownWeeks ?? [];
  const selectedWeekKey = weekKey(selectedWeek);
  const weeklySnapshot = selectedWeekKey ? weeklySnapshots[selectedWeekKey] ?? null : null;
  const seasonSnapshot = bootstrap ? seasonSnapshots[bootstrap.activeSeason] ?? null : null;
  const displayedGames = viewMode === 'season'
    ? selectBestSeasonGames(seasonSnapshot?.games ?? []).map((game) => toViewGame(game))
    : (weeklySnapshot?.games ?? []).map((game) => toViewGame(game));
  const loading = viewMode === 'season' ? seasonLoading : weeklyLoading;
  const error = viewMode === 'season' ? seasonError : weeklyError;
  const hasData = viewMode === 'season' ? seasonSnapshot !== null : weeklySnapshot !== null;
  const handlePreviousWeek = () => {
    if (!selectedWeek) return; const previous = getPreviousSeasonWeek(selectedWeek, knownWeeks);
    if (weekKey(previous) !== weekKey(selectedWeek)) { selectedWasChanged.current = true; setSelectedWeek(previous); }
  };
  const handleNextWeek = () => {
    if (!selectedWeek) return; const next = getNextSeasonWeek(selectedWeek, knownWeeks);
    if (weekKey(next) !== weekKey(selectedWeek)) { selectedWasChanged.current = true; setSelectedWeek(next); }
  };

  if (bootstrapLoading && !bootstrap && !bootstrapError) return <div className="min-h-screen flex flex-col items-center justify-center bg-[#121212] text-white"><Loader2 className="w-8 h-8 text-blue-500 animate-spin mb-4" /><p className="text-xs tracking-widest uppercase text-neutral-500">Loading NFL Season...</p></div>;
  if (!bootstrap && bootstrapError) return <div className="min-h-screen flex flex-col items-center justify-center gap-4 bg-[#121212] text-white"><AlertCircle className="w-8 h-8 text-red-500" /><p className="text-red-200">{bootstrapError}</p><button className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold" onClick={refreshBootstrap}>Retry</button></div>;
  if (!currentWeek || !bootstrap || !sourceCurrentWeek) return null;

  return <div className="min-h-screen flex flex-col bg-[#121212] text-white font-sans">
    <Header currentWeek={currentWeek} currentSeasonLabel={sourceCurrentWeek.seasonLabel} onPreviousWeek={handlePreviousWeek} onNextWeek={handleNextWeek} previousDisabled={isFirstKnownWeek(selectedWeek, knownWeeks)} nextDisabled={isLastKnownWeek(selectedWeek, knownWeeks)} viewMode={viewMode} onViewModeChange={setViewMode} />
    <main className="flex-1 max-w-2xl mx-auto w-full px-4 py-6 relative">
      <div className="flex justify-between items-end mb-4 px-1"><h2 className="text-lg font-bold text-white">{viewMode === 'weekly' ? 'Game Rankings' : 'Season Leaders'}</h2><button onClick={() => setShowRatingInfo(!showRatingInfo)} className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider font-bold text-neutral-500 hover:text-blue-400 transition-colors"><Info className="w-3 h-3" />Rating Info</button></div>
      {showRatingInfo && <div className="mb-6 bg-neutral-800/50 border border-white/10 rounded-xl p-4 text-sm text-neutral-300"><h3 className="font-bold text-white mb-2">How Games Are Rated</h3><p className="text-xs opacity-80">Ratings are calculated from the read API snapshot.</p></div>}
      {bootstrapError && <div role="status" className="mb-4 rounded-lg border border-yellow-900/50 bg-yellow-900/10 p-3 text-sm text-yellow-200">Catalogue refresh failed; showing the last usable catalogue. <button type="button" className="underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400" onClick={refreshBootstrap}>Retry</button></div>}
      {loading && !hasData && <div className="flex flex-col items-center justify-center py-24 gap-4 opacity-60"><Loader2 className="w-8 h-8 text-blue-500 animate-spin" /><p className="text-xs tracking-widest uppercase">{viewMode === 'season' ? 'Analyzing Season Data...' : `Loading ${currentWeek.label}...`}</p></div>}
      {!loading && error && !hasData && <div className="bg-red-900/10 border border-red-900/50 rounded-xl p-6 text-center"><AlertCircle className="w-8 h-8 text-red-500 mx-auto mb-2" /><p className="text-red-200">{error}</p><button className="mt-3 rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold" onClick={() => (viewMode === 'weekly' ? weeklyOwner.current.retry() : seasonOwner.current.retry())}>Retry</button></div>}
      {hasData && error && <div role="status" className="mb-3 rounded-lg border border-yellow-900/50 bg-yellow-900/10 p-3 text-sm text-yellow-200">Showing stale data: {error}</div>}
      {hasData && <div className="flex flex-col gap-3">{displayedGames.length === 0 ? <div className="text-center py-20 text-neutral-500 text-sm">{viewMode === 'season' ? 'No eligible rated games yet.' : 'No games found.'}</div> : displayedGames.map((game) => <GameCard key={game.id} game={game} showWeekContext={viewMode === 'season'} />)}</div>}
      {viewMode === 'weekly' && (isFirstKnownWeek(selectedWeek, knownWeeks) || isLastKnownWeek(selectedWeek, knownWeeks)) && <span className="sr-only">{isFirstKnownWeek(selectedWeek, knownWeeks) ? 'At first known week.' : 'At last known week.'}</span>}
    </main>
  </div>;
};

export default App;
