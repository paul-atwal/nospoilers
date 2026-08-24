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
