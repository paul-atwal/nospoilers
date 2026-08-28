
import React from 'react';
import { ChevronLeft, ChevronRight, Tv, Trophy } from 'lucide-react';
import { WeekInfo } from '../types';

interface HeaderProps {
  currentWeek: WeekInfo;
  currentSeasonLabel: string;
  onPreviousWeek: () => void;
  onNextWeek: () => void;
  viewMode: 'weekly' | 'season';
  onViewModeChange: (mode: 'weekly' | 'season') => void;
}

const Header: React.FC<HeaderProps> = ({
  currentWeek,
  currentSeasonLabel,
  onPreviousWeek,
  onNextWeek,
  viewMode,
  onViewModeChange,
}) => {

  return (
    <header className="sticky top-0 z-50 bg-neutral-900/90 backdrop-blur-md border-b border-white/10 shadow-lg">
      <div className="max-w-2xl mx-auto px-4 py-3">
        <div className="flex items-center justify-between">
          
          {/* Left: Branding */}
          <div className="flex items-center gap-3 cursor-pointer" onClick={() => onViewModeChange('weekly')}>
                <div className="p-1.5 md:p-2 bg-gradient-to-br from-blue-600 to-blue-800 rounded-lg md:rounded-xl shadow-lg shadow-blue-900/20">
                  <Tv className="w-5 h-5 md:w-6 md:h-6 text-white" />
                </div>
                <div>
                  <h1 className="text-lg md:text-xl font-bold tracking-tight text-white leading-none">
                    NoSpoil <span className="text-blue-400">NFL</span>
                  </h1>
                  <p className="hidden xs:block text-[10px] text-neutral-400 mt-1 font-medium tracking-wide">Pure excitement. No spoilers.</p>
                </div>
          </div>

          {/* Right: Actions */}
          <div className="flex items-center gap-2">
              {/* Top 5 Toggle Button */}
              <button
                  onClick={() => onViewModeChange(viewMode === 'season' ? 'weekly' : 'season')}
                  className={`flex items-center gap-2 px-3 py-2 rounded-full text-xs font-bold transition-colors border ${
                      viewMode === 'season' 
                      ? 'bg-yellow-500/20 text-yellow-400 border-yellow-500/50' 
                      : 'bg-neutral-800 text-neutral-400 border-white/5 hover:bg-neutral-700'
                  }`}
              >
                  <Trophy className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Best of Season</span>
                  <span className="sm:hidden">Best</span>
              </button>
          </div>
        </div>

        {/* Navigation Bar - Only show in Weekly Mode */}
        {viewMode === 'weekly' && (
             <div className="mt-3 flex items-center justify-between bg-neutral-800/50 rounded-lg border border-white/5 p-1">
                <button 
                  onClick={onPreviousWeek}
                  className="p-2 hover:bg-white/10 rounded-md transition-colors text-neutral-300"
                  aria-label="Previous week"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                
                <div className="flex flex-col items-center justify-center">
                  <span className="font-bold text-white tracking-wide uppercase text-xs">
                    {currentWeek.label}
                  </span>
                  <span className="text-[10px] font-medium text-blue-400 uppercase">
                    {currentWeek.title}
                  </span>
                </div>

                <button
                  onClick={onNextWeek}
                  className="p-2 hover:bg-white/10 rounded-md transition-colors text-neutral-300"
                  aria-label="Next week"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
            </div>
        )}

        {/* Season Mode Header */}
        {viewMode === 'season' && (
            <div className="mt-3 flex items-center justify-center bg-yellow-900/20 rounded-lg border border-yellow-500/20 p-2">
                <span className="text-xs font-bold text-yellow-500 uppercase tracking-widest">Top Games of {currentSeasonLabel}</span>
            </div>
        )}

      </div>
    </header>
  );
};

export default Header;
