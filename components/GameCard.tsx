
import React, { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Game } from '../types';
import { getExcitementColor } from '../utils/formatting';

interface GameCardProps {
  game: Game;
  showWeekContext?: boolean; // If true, shows "Week 12" instead of "Sun 1:00 PM"
}

const GameCard: React.FC<GameCardProps> = ({ game, showWeekContext = false }) => {
  const [isRevealed, setIsRevealed] = useState(false);
  const colorClasses = getExcitementColor(game.excitementScore);
  
  const isHomeWinner = isRevealed && game.homeScore > game.awayScore;
  const isAwayWinner = isRevealed && game.awayScore > game.homeScore;
  
  // Parse odds nicely if they exist (e.g. "CHI -2.5" -> "CHI" top, "-2.5" bottom)
  const oddsParts = game.odds ? game.odds.split(' ') : null;
  const teamAbbr = oddsParts ? oddsParts[0] : '--';
  const spreadVal = oddsParts ? oddsParts.slice(1).join('') : '';

  // Helper to revert a record string based on game result
  const getPreGameRecord = (record: string, result: 'win' | 'loss' | 'tie') => {
     if (!record) return '';
     
     const parts = record.split('-').map(s => parseInt(s, 10));
     if (parts.some(isNaN)) return record;

     let [w, l, t] = parts;

     if (result === 'win') w = Math.max(0, w - 1);
     if (result === 'loss') l = Math.max(0, l - 1);
     if (result === 'tie') t = Math.max(0, (t || 0) - 1);

     if (t > 0) return `${w}-${l}-${t}`;
     return `${w}-${l}`;
  };

  const isFinal = game.status === 'Final';

  const getDisplayRecord = (currentRecord: string, isHome: boolean) => {
      if (!isFinal) return currentRecord; // Upcoming/Live uses current API record
      if (isRevealed) return currentRecord; // Revealed shows post-game record
      
      // Logic for Hidden Final Game: Show Pre-Game Record
      const homeWon = game.homeScore > game.awayScore;
      const awayWon = game.awayScore > game.homeScore;
      const tied = game.homeScore === game.awayScore;

      let result: 'win' | 'loss' | 'tie' = 'tie';
      if (isHome) {
          if (homeWon) result = 'win';
          else if (awayWon) result = 'loss';
      } else {
          if (awayWon) result = 'win';
          else if (homeWon) result = 'loss';
      }
      if (tied) result = 'tie';

      return getPreGameRecord(currentRecord, result);
  };

  return (
    <div className="bg-neutral-800/40 rounded-xl border border-white/5 overflow-hidden hover:border-white/10 transition-colors shadow-sm">
      <div className="p-3 md:p-4 flex gap-3 md:gap-4">
        
        {/* Left Side: Info + Teams */}
        <div className="flex-1 min-w-0 flex flex-col justify-center py-1">
            
            {/* Meta Row */}
            <div className="flex items-center gap-2 text-[10px] font-bold text-neutral-400 uppercase mb-3 tracking-wider">
                {game.isLive && (
                    <span className="relative flex h-2 w-2 mr-1">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-red-500"></span>
                    </span>
                )}
                
                {game.isUpcoming ? (
                    <>
                       <span className="text-neutral-300">{game.dateLabel}</span>
                       <span className="text-neutral-600">-</span>
                       <span className="text-neutral-500">{game.dayOfWeek} {game.kickoffTime}</span>
                    </>
                ) : (
                    <>
                        <span className={game.isLive ? "text-red-400" : ""}>{game.status}</span>
                        <span className="text-neutral-600">•</span>
                        {showWeekContext ? (
                             <span className="text-blue-400">{game.weekLabel}</span>
                        ) : (
                             <span className="text-neutral-500">{game.dayOfWeek} {game.kickoffTime}</span>
                        )}
                    </>
                )}
                
                {game.broadcaster && !showWeekContext && (
                   <>
                    <span className="text-neutral-600 hidden xs:inline">•</span>
                    <span className="text-neutral-500 hidden xs:inline">{game.broadcaster}</span>
                   </>
                )}
            </div>

            {/* Teams Stack */}
            <div className="flex flex-col gap-3">
                
                {/* Away Team */}
                <div className="flex items-center justify-between pr-2">
                    <div className="flex items-center gap-3 min-w-0">
                        {game.awayTeamLogo ? (
                            <img src={game.awayTeamLogo} alt={game.awayTeam} className="w-7 h-7 md:w-8 md:h-8 object-contain" />
                        ) : (
                            <div className="w-7 h-7 md:w-8 md:h-8 rounded-full bg-neutral-700 flex items-center justify-center text-xs font-bold">{game.awayTeam.charAt(0)}</div>
                        )}
                        <div className="flex flex-col leading-none gap-1">
                            <span className={`text-sm md:text-base font-bold truncate ${isRevealed ? (isAwayWinner ? 'text-white' : 'text-neutral-500') : 'text-neutral-200'}`}>
                                {game.awayTeam}
                            </span>
                            <span className="text-[10px] text-neutral-500 font-medium">
                                {getDisplayRecord(game.awayRecord, false)}
                            </span>
                        </div>
                    </div>
                    {isRevealed && (
                        <span className={`font-mono font-bold text-lg ${isAwayWinner ? 'text-white' : 'text-neutral-600'}`}>
                            {game.awayScore}
                        </span>
                    )}
                </div>

                {/* Home Team */}
                <div className="flex items-center justify-between pr-2">
                     <div className="flex items-center gap-3 min-w-0">
                        {game.homeTeamLogo ? (
                            <img src={game.homeTeamLogo} alt={game.homeTeam} className="w-7 h-7 md:w-8 md:h-8 object-contain" />
                        ) : (
                            <div className="w-7 h-7 md:w-8 md:h-8 rounded-full bg-neutral-700 flex items-center justify-center text-xs font-bold">{game.homeTeam.charAt(0)}</div>
                        )}
                        <div className="flex flex-col leading-none gap-1">
                            <span className={`text-sm md:text-base font-bold truncate ${isRevealed ? (isHomeWinner ? 'text-white' : 'text-neutral-500') : 'text-neutral-200'}`}>
                                {game.homeTeam}
                            </span>
                             <span className="text-[10px] text-neutral-500 font-medium">
                                {getDisplayRecord(game.homeRecord, true)}
                            </span>
                        </div>
                    </div>
                     {isRevealed && (
                        <span className={`font-mono font-bold text-lg ${isHomeWinner ? 'text-white' : 'text-neutral-600'}`}>
                            {game.homeScore}
                        </span>
                    )}
                </div>
            </div>
        </div>

        {/* Right Side: Score & Actions */}
        <div className="flex flex-col items-center justify-between min-w-[60px] border-l border-white/5 pl-3 md:pl-4 py-1">
            
            {/* Score/Odds Circle */}
            <div className="flex-1 flex items-start justify-center pt-1">
                {!game.isUpcoming ? (
                    <div className={`w-12 h-12 md:w-14 md:h-14 rounded-full flex items-center justify-center border-[3px] backdrop-blur-sm shadow-lg ${colorClasses}`}>
                        <span className={`font-black text-lg md:text-xl leading-none ${colorClasses.split(' ')[0]}`}>
                            {game.excitementScore.toFixed(1)}
                        </span>
                    </div>
                ) : (
                    <div className="w-12 h-12 md:w-14 md:h-14 rounded-full bg-neutral-800/50 border-2 border-neutral-700 flex flex-col items-center justify-center text-[10px] text-neutral-400 font-bold leading-tight text-center p-1">
                        {game.odds ? (
                            <>
                                <span className="block mb-0.5 text-neutral-500">{teamAbbr}</span>
                                <span className="text-neutral-300">{spreadVal}</span>
                            </>
                        ) : (
                            <span>--</span>
                        )}
                    </div>
                )}
            </div>

            {/* Reveal Button (Compact Icon) */}
            {!game.isUpcoming && (
                <button 
                    onClick={() => setIsRevealed(!isRevealed)}
                    className={`mt-2 p-2 rounded-full transition-all duration-200 focus:outline-none
                        ${isRevealed ? 'text-neutral-600 hover:bg-neutral-800' : 'text-blue-400 hover:bg-blue-500/10 hover:text-blue-300'}`}
                    aria-label={isRevealed ? "Hide Score" : "Reveal Score"}
                >
                    {isRevealed ? <EyeOff size={20} /> : <Eye size={20} />}
                </button>
            )}
             {/* Spacer for alignment */}
            {game.isUpcoming && <div className="h-8 w-8"></div>}
        </div>

      </div>
    </div>
  );
};

export default GameCard;
