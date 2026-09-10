import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import Header from '../../components/Header';

afterEach(cleanup);

describe('Header catalogue navigation', () => {
  it('uses native disabled controls at catalogue ends', () => {
    render(<Header
      currentWeek={{ seasonWeek: { season: 2020, phase: 'preseason', week: 1 }, title: 'Preseason', label: 'Week 1', seasonLabel: '2020-21' }}
      currentSeasonLabel="2020-21"
      onPreviousWeek={() => undefined}
      onNextWeek={() => undefined}
      previousDisabled
      nextDisabled
      viewMode="weekly"
      onViewModeChange={() => undefined}
    />);

    expect((screen.getByRole('button', { name: 'Previous week (first known week)' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Next week (last known week)' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('names and exposes the active Best of Season state', () => {
    const onViewModeChange = vi.fn();
    const props = {
      currentWeek: { seasonWeek: { season: 2026, phase: 'regular_season' as const, week: 1 }, title: 'Regular Season', label: 'Week 1', seasonLabel: '2026-27' },
      currentSeasonLabel: '2026-27',
      onPreviousWeek: () => undefined,
      onNextWeek: () => undefined,
      onViewModeChange,
    };

    const { rerender } = render(<Header {...props} viewMode="weekly" />);
    const showSeason = screen.getByRole('button', { name: 'Show Best of Season' });
    expect(showSeason.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(showSeason);
    expect(onViewModeChange).toHaveBeenCalledWith('season');

    rerender(<Header {...props} viewMode="season" />);
    const showWeekly = screen.getByRole('button', { name: 'Return to weekly games' });
    expect(showWeekly.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(showWeekly);
    expect(onViewModeChange).toHaveBeenLastCalledWith('weekly');
  });
});
