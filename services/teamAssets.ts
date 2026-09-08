import type { ApiTeam } from '../types';

/** Asset map version is bumped whenever an identity or bundled file changes. */
export const TEAM_ASSET_MAP_VERSION = '2026-09-08';

const TEAM_ASSETS: Readonly<Record<string, string>> = {
  sea: '/team-logos/sea.svg',
  ne: '/team-logos/ne.svg',
};

export const getTeamLogoUrl = (team: Pick<ApiTeam, 'id' | 'logoKey'>): string | null => {
  if (team.logoKey && TEAM_ASSETS[team.logoKey]) return TEAM_ASSETS[team.logoKey];
  return null;
};

export const getTeamFallbackLabel = (team: Pick<ApiTeam, 'displayName' | 'abbreviation'>): string => (
  team.abbreviation || team.displayName.slice(0, 1).toUpperCase() || '?'
);

