import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
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
});
