import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import GameCard from '../../components/GameCard';
import type { GameRecordSnapshots, RecordScope } from '../../types';
import { makeGame } from './fixtures/game';

afterEach(cleanup);

const makeRecords = (
  pregame: readonly [number, number, number?],
  postgame?: readonly [number, number, number?],
  scope: RecordScope = 'regular_season',
): GameRecordSnapshots => ({
  pregame: {
    record: {
      wins: pregame[0],
      losses: pregame[1],
      ties: pregame[2] ?? 0,
    },
    scope,
  },
  ...(postgame ? {
    postgame: {
      record: {
        wins: postgame[0],
        losses: postgame[1],
        ties: postgame[2] ?? 0,
      },
      scope,
    },
  } : {}),
});

describe('GameCard scores', () => {
  it('hides scores until reveal', () => {
    render(<GameCard game={makeGame()} />);

    expect(screen.queryByText('24')).toBeNull();
    expect(screen.queryByText('17')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Reveal score.*Away Team.*Home Team/i }));

    expect(screen.getByText('24')).not.toBeNull();
    expect(screen.getByText('17')).not.toBeNull();
  });

  it('updates a revealed score when game props change', () => {
    const { rerender } = render(<GameCard game={makeGame()} />);
    fireEvent.click(screen.getByRole('button', { name: /Reveal score.*Away Team.*Home Team/i }));

    rerender(
      <GameCard
        game={makeGame({
          homeScore: 27,
          awayScore: 20,
          spoilerData: {
            homeScore: 27,
            awayScore: 20,
            summary: 'Away Team 20 @ Home Team 27',
          },
        })}
      />,
    );

    expect(screen.queryByText('24')).toBeNull();
    expect(screen.queryByText('17')).toBeNull();
    expect(screen.getByText('27')).not.toBeNull();
    expect(screen.getByText('20')).not.toBeNull();
  });
});

describe('GameCard records', () => {
  it('shows pregame records while hidden and postgame records after reveal', () => {
    render(<GameCard game={makeGame()} />);

    expect(screen.getByText('8-2')).not.toBeNull();
    expect(screen.getByText('7-3')).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Reveal score.*Away Team.*Home Team/i }));

    expect(screen.getByText('9-2')).not.toBeNull();
    expect(screen.getByText('7-4')).not.toBeNull();
  });

  it('shows pregame records for a hidden regular-season tie', () => {
    render(
      <GameCard
        game={makeGame({
          homeScore: 20,
          awayScore: 20,
          homeRecord: makeRecords([8, 2], [8, 2, 1]),
          awayRecord: makeRecords([7, 3], [7, 3, 1]),
        })}
      />,
    );

    expect(screen.getByText('8-2')).not.toBeNull();
    expect(screen.getByText('7-3')).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Reveal score.*Away Team.*Home Team/i }));

    expect(screen.getByText('8-2-1')).not.toBeNull();
    expect(screen.getByText('7-3-1')).not.toBeNull();
  });

  it('keeps regular-season playoff records unchanged after reveal', () => {
    render(
      <GameCard
        game={makeGame({
          homeRecord: makeRecords([12, 6]),
          awayRecord: makeRecords([12, 6]),
          seasonWeek: {
            season: 2026,
            phase: 'postseason',
            week: 1,
          },
        })}
      />,
    );

    expect(screen.getAllByText('12-6')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: /Reveal score.*Away Team.*Home Team/i }));

    expect(screen.getAllByText('12-6')).toHaveLength(2);
  });

  it('shows an unavailable marker instead of inventing a record', () => {
    render(<GameCard game={makeGame({ homeRecord: null, awayRecord: null })} />);

    expect(screen.getAllByText('--')).toHaveLength(2);
  });
});

describe('GameCard week context', () => {
  it('shows postponed status instead of presenting it as a scheduled date', () => {
    render(<GameCard game={makeGame({
      status: 'Postponed',
      isUpcoming: true,
      isScheduled: false,
      excitementScore: null,
      rating: { state: 'pending', label: 'Rating pending', score: null },
    })} />);
    expect(screen.getByText('Postponed')).not.toBeNull();
    expect(screen.getByText('Rating pending')).not.toBeNull();
  });

  it('shows the shared postseason label in the season view', () => {
    render(
      <GameCard
        game={makeGame({
          seasonWeek: {
            season: 2026,
            phase: 'postseason',
            week: 1,
          },
        })}
        showWeekContext
      />,
    );

    expect(screen.getByText('Wild Card')).not.toBeNull();
  });
});

describe('GameCard identity and accessibility safety', () => {
  it('resets revealed values synchronously when reused for another game identity', () => {
    const { rerender } = render(<GameCard game={makeGame()} />);
    fireEvent.click(screen.getByRole('button', { name: /Reveal score.*Away Team.*Home Team/i }));
    expect(screen.getByLabelText('Home Team score 24')).toBeTruthy();
    rerender(<GameCard game={makeGame({ id: 'game-2', homeTeam: 'New Home', awayTeam: 'New Away', homeScore: 31, awayScore: 10 })} />);
    expect(screen.queryByLabelText(/score 31/i)).toBeNull();
    expect(screen.getByRole('button', { name: /Reveal score.*New Away.*New Home/i })).toBeTruthy();
  });

  it('does not expose hidden scores in accessible names and disables reveal when a score is missing', () => {
    render(<GameCard game={makeGame({ homeScore: null, awayScore: 10 })} />);
    expect(screen.queryByLabelText(/score 24/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /Reveal score/i })).toBeNull();
  });

  it('falls back to the supplied abbreviation when a local logo fails', () => {
    render(<GameCard game={makeGame({ home: { id: '26', name: 'Home Team', abbreviation: 'HME', logoUrl: '/team-logos/26.png', records: null } })} />);
    fireEvent.error(screen.getByRole('img', { name: 'Home Team logo' }));
    expect(screen.queryByRole('img', { name: 'Home Team logo' })).toBeNull();
    expect(screen.getByLabelText('Home Team abbreviation').textContent).toBe('HME');
  });
});
