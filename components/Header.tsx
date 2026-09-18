import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight, Tv, Trophy } from 'lucide-react';
import { WeekInfo } from '../types';

interface HeaderProps {
  currentWeek: WeekInfo;
  selectedSeason: number;
  availableSeasons: readonly number[];
  onSeasonChange: (season: number) => void;
  onPreviousWeek: () => void;
  onNextWeek: () => void;
  previousDisabled?: boolean;
  nextDisabled?: boolean;
  viewMode: 'weekly' | 'season';
  onViewModeChange: (mode: 'weekly' | 'season') => void;
}

interface SeasonPickerProps {
  currentWeekLabel: string;
  selectedSeason: number;
  availableSeasons: readonly number[];
  onSeasonChange: (season: number) => void;
  highlighted?: boolean;
}

const SeasonPicker: React.FC<SeasonPickerProps> = ({
  currentWeekLabel,
  selectedSeason,
  availableSeasons,
  onSeasonChange,
  highlighted = false,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Record<number, HTMLButtonElement | null>>({});
  const [open, setOpen] = useState(false);

  const selectedIndex = Math.max(0, availableSeasons.indexOf(selectedSeason));
  const focusOption = (index: number) => {
    if (availableSeasons.length === 0) return;
    const nextIndex = Math.min(Math.max(index, 0), availableSeasons.length - 1);
    optionRefs.current[availableSeasons[nextIndex]]?.focus();
  };

  const close = (returnFocus = false) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  };

  const openAndFocus = (index = selectedIndex) => {
    setOpen(true);
    queueMicrotask(() => focusOption(index));
  };

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close(true);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const selectSeason = (season: number) => {
    onSeasonChange(season);
    close(true);
  };

  return (
    <div ref={containerRef} className="relative min-w-0">
      <button
        ref={triggerRef}
        type="button"
        data-testid="season-picker-trigger"
        aria-label={`Select season, currently ${selectedSeason}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => (open ? close() : openAndFocus())}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openAndFocus();
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            openAndFocus(availableSeasons.length - 1);
          }
        }}
        className={`relative flex min-w-36 flex-col items-center rounded-md px-3 py-1.5 transition-colors hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${highlighted ? 'text-yellow-400' : 'text-white'}`}
      >
        <span className="relative flex w-full items-center justify-center font-bold tracking-wide uppercase text-xs">
          <span data-testid="season-picker-label" className="max-w-[calc(100%-1.5rem)] truncate text-center">{currentWeekLabel}</span>
          <ChevronDown data-testid="season-picker-chevron" className={`pointer-events-none absolute right-0 h-3 w-3 transition-transform ${open ? 'rotate-180' : ''}`} />
        </span>
        <span data-testid="season-picker-year" className={`text-[10px] font-medium uppercase ${highlighted ? 'text-yellow-400' : 'text-blue-400'}`}>
          {selectedSeason}
        </span>
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Available seasons"
          className="absolute left-1/2 top-full z-50 mt-2 max-h-[min(16rem,calc(100vh-8rem))] w-[min(18rem,calc(100vw-2rem))] -translate-x-1/2 overflow-y-auto rounded-lg border border-white/10 bg-neutral-900 p-1 shadow-2xl"
          onKeyDown={(event) => {
            const index = availableSeasons.indexOf(Number((event.target as HTMLElement).dataset.season));
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              focusOption((index + 1) % availableSeasons.length);
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              focusOption((index - 1 + availableSeasons.length) % availableSeasons.length);
            } else if (event.key === 'Home') {
              event.preventDefault();
              focusOption(0);
            } else if (event.key === 'End') {
              event.preventDefault();
              focusOption(availableSeasons.length - 1);
            } else if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              if (index >= 0) selectSeason(availableSeasons[index]);
            } else if (event.key === 'Escape') {
              event.preventDefault();
              close(true);
            }
          }}
        >
          {availableSeasons.map((season) => (
            <button
              key={season}
              ref={(element) => { optionRefs.current[season] = element; }}
              type="button"
              role="option"
              data-season={season}
              aria-selected={season === selectedSeason}
              tabIndex={season === selectedSeason ? 0 : -1}
              onClick={() => selectSeason(season)}
              className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${season === selectedSeason ? 'bg-blue-600/20 text-blue-300' : 'text-neutral-200 hover:bg-white/10'}`}
            >
              <span>{season}</span>
              {season === selectedSeason && <span aria-hidden="true" className="text-[10px] uppercase tracking-wider text-blue-400">Selected</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

const Header: React.FC<HeaderProps> = ({
  currentWeek,
  selectedSeason,
  availableSeasons,
  onSeasonChange,
  onPreviousWeek,
  onNextWeek,
  previousDisabled = false,
  nextDisabled = false,
  viewMode,
  onViewModeChange,
}) => (
  <header className="sticky top-0 z-50 bg-neutral-900/90 backdrop-blur-md border-b border-white/10 shadow-lg">
    <div className="max-w-2xl mx-auto px-4 py-3">
      <div className="flex items-center justify-between">
        <button
          type="button"
          aria-label="Show weekly games"
          className="flex items-center gap-3 cursor-pointer text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 rounded-lg"
          onClick={() => onViewModeChange('weekly')}
        >
          <div className="p-1.5 md:p-2 bg-gradient-to-br from-blue-600 to-blue-800 rounded-lg md:rounded-xl shadow-lg shadow-blue-900/20">
            <Tv className="w-5 h-5 md:w-6 md:h-6 text-white" />
          </div>
          <div>
            <h1 className="text-lg md:text-xl font-bold tracking-tight text-white leading-none">
              NoSpoil <span className="text-blue-400">NFL</span>
            </h1>
          </div>
        </button>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onViewModeChange(viewMode === 'season' ? 'weekly' : 'season')}
            aria-label={viewMode === 'season' ? 'Return to weekly games' : 'Show Best of Season'}
            aria-pressed={viewMode === 'season'}
            className={`flex items-center gap-2 px-3 py-2 rounded-full text-xs font-bold transition-colors border ${viewMode === 'season' ? 'bg-yellow-500/20 text-yellow-400 border-yellow-500/50' : 'bg-neutral-800 text-neutral-400 border-white/5 hover:bg-neutral-700'} focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400`}
          >
            <Trophy className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Best of Season</span>
            <span className="sm:hidden">Best</span>
          </button>
        </div>
      </div>

      {viewMode === 'weekly' ? (
        <div className="mt-3 flex items-center justify-between bg-neutral-800/50 rounded-lg border border-white/5 p-1">
          <button
            type="button"
            onClick={onPreviousWeek}
            disabled={previousDisabled}
            className="p-2 hover:bg-white/10 rounded-md transition-colors text-neutral-300 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
            aria-label={previousDisabled ? 'Previous week (only available week)' : 'Previous week'}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>

          <SeasonPicker
            currentWeekLabel={currentWeek.label}
            selectedSeason={selectedSeason}
            availableSeasons={availableSeasons}
            onSeasonChange={onSeasonChange}
          />

          <button
            type="button"
            onClick={onNextWeek}
            disabled={nextDisabled}
            className="p-2 hover:bg-white/10 rounded-md transition-colors text-neutral-300 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
            aria-label={nextDisabled ? 'Next week (only available week)' : 'Next week'}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      ) : (
        <div className="mt-3 flex items-center justify-center bg-yellow-900/20 rounded-lg border border-yellow-500/20 p-1">
          <SeasonPicker
            currentWeekLabel="Best of Season"
            selectedSeason={selectedSeason}
            availableSeasons={availableSeasons}
            onSeasonChange={onSeasonChange}
            highlighted
          />
        </div>
      )}
    </div>
  </header>
);

export default Header;
