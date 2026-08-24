import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import GameCard from '../../components/GameCard';
import { makeGame } from './fixtures/game';

afterEach(cleanup);

describe('GameCard scores', () => {
  it('hides scores until reveal', () => {
    render(<GameCard game={makeGame()} />);

    expect(screen.queryByText('24')).toBeNull();
    expect(screen.queryByText('17')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Reveal Score' }));

    expect(screen.getByText('24')).not.toBeNull();
    expect(screen.getByText('17')).not.toBeNull();
  });

  it('updates a revealed score when game props change', () => {
    const { rerender } = render(<GameCard game={makeGame()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reveal Score' }));

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
    render(<GameCard game={makeGame({ homeRecord: '9-2', awayRecord: '7-4' })} />);

    expect(screen.getByText('8-2')).not.toBeNull();
    expect(screen.getByText('7-3')).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Reveal Score' }));

    expect(screen.getByText('9-2')).not.toBeNull();
    expect(screen.getByText('7-4')).not.toBeNull();
  });

  it('shows pregame records for a hidden regular-season tie', () => {
    render(
      <GameCard
        game={makeGame({
          homeScore: 20,
          awayScore: 20,
          homeRecord: '8-2-1',
          awayRecord: '7-3-1',
        })}
      />,
    );

    expect(screen.getByText('8-2')).not.toBeNull();
    expect(screen.getByText('7-3')).not.toBeNull();
  });

  it('keeps regular-season records unchanged for a postseason game', () => {
    render(
      <GameCard
        game={makeGame({
          homeRecord: '12-5',
          awayRecord: '11-6',
          seasonType: 3,
          weekLabel: 'Wild Card',
        })}
      />,
    );

    expect(screen.getByText('12-5')).not.toBeNull();
    expect(screen.getByText('11-6')).not.toBeNull();
  });
});
