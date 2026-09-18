import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import Header from '../../components/Header';

afterEach(cleanup);

describe('Header catalogue navigation', () => {
  it('uses native disabled controls at catalogue ends', () => {
    render(<Header
      currentWeek={{ seasonWeek: { season: 2020, phase: 'preseason', week: 1 }, title: 'Preseason', label: 'Week 1', seasonLabel: '2020-21' }}
      selectedSeason={2020}
      availableSeasons={[2020]}
      onSeasonChange={() => undefined}
      onPreviousWeek={() => undefined}
      onNextWeek={() => undefined}
      previousDisabled
      nextDisabled
      viewMode="weekly"
      onViewModeChange={() => undefined}
    />);

    expect((screen.getByRole('button', { name: 'Previous week (only available week)' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Next week (only available week)' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('names and exposes the active Best of Season state', () => {
    const onViewModeChange = vi.fn();
    const props = {
      currentWeek: { seasonWeek: { season: 2026, phase: 'regular_season' as const, week: 1 }, title: 'Regular Season', label: 'Week 1', seasonLabel: '2026-27' },
      selectedSeason: 2026,
      availableSeasons: [2026, 2025],
      onSeasonChange: () => undefined,
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
    expect(screen.getByTestId('season-picker-label').textContent).toBe('2026');
    expect(screen.queryByTestId('season-picker-year')).toBeNull();
    fireEvent.click(showWeekly);
    expect(onViewModeChange).toHaveBeenLastCalledWith('weekly');
  });

  it('supports keyboard season selection and returns focus after Escape', async () => {
    const onSeasonChange = vi.fn();
    render(<Header
      currentWeek={{ seasonWeek: { season: 2026, phase: 'regular_season', week: 1 }, title: 'Regular Season', label: 'Week 1', seasonLabel: '2026-27' }}
      selectedSeason={2026}
      availableSeasons={[2026, 2025, 2024]}
      onSeasonChange={onSeasonChange}
      onPreviousWeek={() => undefined}
      onNextWeek={() => undefined}
      viewMode="weekly"
      onViewModeChange={() => undefined}
    />);

    const trigger = screen.getByRole('button', { name: 'Select season, currently 2026' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(await screen.findByRole('listbox', { name: 'Available seasons' })).toBeTruthy();
    const selected = screen.getByRole('option', { name: '2026' });
    expect(document.activeElement).toBe(selected);
    fireEvent.keyDown(selected, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('option', { name: '2025' }));
    fireEvent.keyDown(screen.getByRole('option', { name: '2025' }), { key: 'Enter' });
    expect(onSeasonChange).toHaveBeenCalledWith(2025);
    expect(document.activeElement).toBe(trigger);

    fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.keyDown(trigger, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(document.activeElement).toBe(trigger);

    fireEvent.click(trigger);
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Show Best of Season' }));
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
