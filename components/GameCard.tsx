import React, { useEffect, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import type {
  Game,
  GameRecordSnapshots,
  RatingPresentation,
  TeamView,
} from '../types';
import { getTeamDisplayName } from '../services/teamAssets';
import { getExcitementColor } from '../utils/formatting';
import { formatTeamRecord } from '../utils/records';
import { getWeekInfo } from '../utils/scheduleWeek';

interface GameCardProps {
  game: Game;
  showWeekContext?: boolean;
}

const fallbackTeam = (
  name: string,
  logoUrl: string | undefined,
  records: GameRecordSnapshots | null,
): TeamView => ({
  id: name,
  name: getTeamDisplayName({ id: name, displayName: name }),
  abbreviation: name.slice(0, 3).toUpperCase(),
  logoUrl: logoUrl ?? null,
  records,
});

const GameCard: React.FC<GameCardProps> = ({ game, showWeekContext = false }) => {
  const [isRevealed, setIsRevealed] = useState(false);
  const [imageFailed, setImageFailed] = useState({ home: false, away: false });
  const [revealedGameId, setRevealedGameId] = useState<string | null>(null);

  useEffect(() => {
    if (revealedGameId !== null && revealedGameId !== game.id) {
      setRevealedGameId(null);
      setIsRevealed(false);
      setImageFailed({ home: false, away: false });
    }
  }, [game.id, revealedGameId]);

  const home = game.home ?? fallbackTeam(game.homeTeam, game.homeTeamLogo, game.homeRecord);
  const away = game.away ?? fallbackTeam(game.awayTeam, game.awayTeamLogo, game.awayRecord);
  const rating: RatingPresentation = game.rating ?? {
    state: game.excitementScore === null ? 'pending' : 'confirmed',
    label: game.excitementScore === null ? 'Rating pending' : 'Confirmed rating',
    score: game.excitementScore,
  };
  const scoreAvailable = game.homeScore !== null && game.awayScore !== null;
  const canReveal = scoreAvailable && !game.isUpcoming;
  const revealActive = revealedGameId === game.id && isRevealed;
  const score = rating.score;
  const isScheduled = game.isScheduled ?? game.isUpcoming;
  const colorClasses = score === null
    ? 'text-neutral-500 border-white/10 bg-white/5'
    : getExcitementColor(score);
  const weekInfo = getWeekInfo(game.seasonWeek);
  const isUnratedPreseason = game.seasonWeek.phase === 'preseason'
    && rating.state === 'pending'
    && rating.score === null
    && rating.confirmationSupported === false;
  const ratingMessage = isUnratedPreseason || rating.state === 'confirmed'
    || game.isLive || game.isDelayed
    ? null
    : rating.score === null ? rating.label : null;

  const record = (snapshots: GameRecordSnapshots | null | undefined): string => {
    if (!snapshots) return '--';
    const selected = revealActive
      ? snapshots.postgame ?? snapshots.pregame
      : snapshots.pregame;
    return formatTeamRecord(selected.record);
  };

  const TeamRow = ({
    side,
    team,
    teamScore,
  }: {
    side: 'home' | 'away';
    team: TeamView;
    teamScore: number | null;
  }) => {
    const failed = imageFailed[side];
    const winner = side === 'home'
      ? game.homeScore! > game.awayScore!
      : game.awayScore! > game.homeScore!;

    return (
      <div className="flex items-center justify-between pr-2">
        <div className="flex items-center gap-3 min-w-0">
          {team.logoUrl && !failed ? (
            <img
              src={team.logoUrl}
              alt={`${team.name} logo`}
              onError={() => setImageFailed((previous) => ({ ...previous, [side]: true }))}
              className="w-7 h-7 md:w-8 md:h-8 object-contain"
            />
          ) : (
            <div
              aria-label={`${team.name} abbreviation`}
              className="w-7 h-7 md:w-8 md:h-8 rounded-full bg-neutral-700 flex items-center justify-center text-[10px] font-bold"
            >
              {team.abbreviation}
            </div>
          )}
          <div className="flex flex-col leading-none gap-1">
            <span
              className={`text-sm md:text-base font-bold truncate ${
                revealActive
                  ? winner ? 'text-white' : 'text-neutral-500'
                  : 'text-neutral-200'
              }`}
            >
              {team.name}
            </span>
            <span className="text-[10px] text-neutral-500 font-medium">
              {record(team.records)}
            </span>
          </div>
        </div>
        {revealActive && (
          <span
            aria-label={`${team.name} score ${teamScore}`}
            className={`font-mono font-bold text-lg ${winner ? 'text-white' : 'text-neutral-600'}`}
          >
            {teamScore}
          </span>
        )}
      </div>
    );
  };

  const oddsParts = game.odds?.trim().split(/\s+/) ?? [];
  const favoriteLabel = oddsParts.length > 1 ? oddsParts[0] : '';
  const spreadLabel = oddsParts.length > 1 ? oddsParts.slice(1).join(' ') : oddsParts[0] || '--';
  const oddsLabel = favoriteLabel ? `${favoriteLabel} ${spreadLabel}` : spreadLabel;

  return (
    <article className="bg-neutral-800/40 rounded-xl border border-white/5 overflow-hidden hover:border-white/10 transition-colors shadow-sm">
      <div className="p-3 md:p-4 flex gap-3 md:gap-4">
        <div
          id={`game-${game.id}-teams`}
          className="flex-1 min-w-0 flex flex-col justify-center py-1"
        >
          <div
            data-testid={`game-${game.id}-metadata`}
            className="flex items-center gap-2 text-[10px] font-bold text-neutral-400 uppercase mb-3 tracking-wider"
          >
            {game.isLive && (
              <span className="relative flex h-2 w-2 mr-1 shrink-0">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-red-500" />
              </span>
            )}
            <span className={game.isLive ? 'text-red-400' : ''}>
              {isScheduled ? game.dateLabel || 'Date TBD' : game.status}
            </span>
            <span className="text-neutral-600">•</span>
            {showWeekContext ? (
              <span className="text-blue-400">{weekInfo.label}</span>
            ) : (
              <span className="text-neutral-500">
                {game.kickoff?.time === 'Kickoff time TBD'
                  ? game.kickoff.time
                  : `${game.dayOfWeek} ${game.kickoffTime}`}
              </span>
            )}
            {game.broadcaster && !showWeekContext && (
              <span className="text-neutral-500 hidden sm:inline">{game.broadcaster}</span>
            )}
          </div>
          <div className="flex flex-col gap-3">
            <TeamRow side="away" team={away} teamScore={game.awayScore} />
            <TeamRow side="home" team={home} teamScore={game.homeScore} />
          </div>
        </div>
        <div className="flex flex-col items-center justify-between min-w-[68px] border-l border-white/5 pl-3 md:pl-4 py-1">
          <div className="flex-1 w-full flex items-center justify-center relative">
            <div
              aria-label={isScheduled ? `Spread ${oddsLabel}` : game.isLive ? 'Live game' : `Rating ${score ?? 'pending'}`}
              className={`relative w-12 h-12 md:w-14 md:h-14 rounded-full flex flex-col items-center justify-center border-[3px] ${
                isScheduled
                  ? 'text-neutral-400 border-neutral-700 bg-neutral-800/50'
                  : colorClasses
              }`}
            >
              {isScheduled ? (
                <>
                  {favoriteLabel && (
                    <span className="text-[9px] font-bold text-center leading-tight">{favoriteLabel}</span>
                  )}
                  <span className="text-[10px] font-bold text-center">{spreadLabel}</span>
                </>
              ) : (
                <>
                  {game.isLive ? (
                    <span className="flex items-center gap-1 text-[9px] font-bold tracking-wider text-red-400">
                      <span className="relative flex h-1.5 w-1.5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                        <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-red-500" />
                      </span>
                      LIVE
                    </span>
                  ) : game.isDelayed ? (
                    <span className="text-[10px] font-bold text-center">--</span>
                  ) : ratingMessage && (
                    <span className="text-[9px] font-bold text-center leading-tight">
                      {ratingMessage}
                    </span>
                  )}
                  {isUnratedPreseason && <span className="text-[10px] font-bold text-center">--</span>}
                  {!game.isLive && score !== null && (
                    <span className="font-black text-lg leading-none">{score.toFixed(1)}</span>
                  )}
                </>
              )}
            </div>
          </div>
          {canReveal && (
            <button
              type="button"
              onClick={() => {
                setRevealedGameId(game.id);
                setIsRevealed((value) => !value);
              }}
              className={`mt-1 p-2 rounded-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${
                revealActive
                  ? 'text-neutral-600 hover:bg-neutral-800'
                  : 'text-blue-400 hover:bg-blue-500/10 hover:text-blue-300'
              }`}
              aria-label={revealActive
                ? `Hide score for ${away.name} at ${home.name}`
                : `Reveal score for ${away.name} at ${home.name}`}
              aria-describedby={`game-${game.id}-teams`}
              aria-pressed={revealActive}
            >
              {revealActive ? <EyeOff size={20} /> : <Eye size={20} />}
            </button>
          )}
        </div>
      </div>
    </article>
  );
};

export default GameCard;
