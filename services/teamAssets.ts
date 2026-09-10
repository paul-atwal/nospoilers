import type { ApiTeam } from '../types';

/** Versioned against the backend provider's stable numeric ESPN team IDs. */
export const TEAM_ASSET_MAP_VERSION = '2026-09-08-nfl32-v2';
export interface TeamAsset { readonly id: string; readonly abbreviation: string; readonly url: string; }
const TEAMS: readonly TeamAsset[] = [
  ['1', 'ATL'], ['2', 'BUF'], ['3', 'CHI'], ['4', 'CIN'], ['5', 'CLE'], ['6', 'DAL'], ['7', 'DEN'], ['8', 'DET'],
  ['9', 'GB'], ['10', 'TEN'], ['11', 'IND'], ['12', 'KC'], ['13', 'LV'], ['14', 'LAR'], ['15', 'MIA'], ['16', 'MIN'],
  ['17', 'NE'], ['18', 'NO'], ['19', 'NYG'], ['20', 'NYJ'], ['21', 'PHI'], ['22', 'ARI'], ['23', 'PIT'], ['24', 'LAC'],
  ['25', 'SF'], ['26', 'SEA'], ['27', 'TB'], ['28', 'WSH'], ['29', 'CAR'], ['30', 'JAX'], ['33', 'BAL'], ['34', 'HOU'],
].map(([id, abbreviation]) => ({ id, abbreviation, url: `/team-logos/${id}.png` }));
const TEAM_ASSETS = new Map(TEAMS.map((team) => [team.id, team]));
const ALIASES: Readonly<Record<string, string>> = { sea: '26', ne: '17' };

export const getTeamLogoUrl = (team: Pick<ApiTeam, 'id' | 'logoKey'>): string | null => {
  const id = TEAM_ASSETS.has(team.id) ? team.id : (team.logoKey && (TEAM_ASSETS.has(team.logoKey) ? team.logoKey : ALIASES[team.logoKey]));
  return id ? TEAM_ASSETS.get(id)?.url ?? null : null;
};
export const getTeamFallbackLabel = (team: Pick<ApiTeam, 'displayName' | 'abbreviation'>): string => team.abbreviation || team.displayName.slice(0, 1).toUpperCase() || '?';
export const getKnownTeamAssets = (): readonly TeamAsset[] => TEAMS;
