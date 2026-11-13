/**
 * Workshop community-groups API client.
 *
 * Backend lives at /api/groups in the gateway; workshop's /gw/{path}
 * proxy strips the `api/` prefix, so client paths begin with /gw/groups.
 */
import { authFetch } from './useAuth';

const GW = '/gw/groups';

export type GroupRow = {
  id: string;
  name: string;
  description: string;
  visibility: 'open' | 'invite-only' | string;
  created_at: string;
  member_count: number;
  artifact_count: number;
  last_activity_at: string | null;
  i_am_a_member: boolean;
};

export type GroupMember = {
  kc_sub: string;
  role: 'owner' | 'admin' | 'member';
  joined_at: string;
  username: string | null;
};

export type GroupArtifact = {
  id: string;
  kind: string;
  title: string;
  description: string | null;
  peer_review_status: string | null;
  created_at: string;
  updated_at: string | null;
};

export type GroupDetail = {
  group: {
    id: string; name: string; description: string;
    visibility: string; created_at: string;
    owner_username: string | null;
  };
  members:   GroupMember[];
  artifacts: GroupArtifact[];
};

async function _json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await authFetch(input, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export async function listGroups(): Promise<GroupRow[]> {
  const j = await _json<{ groups: GroupRow[] }>(GW);
  return j.groups;
}

export function getGroup(id: string): Promise<GroupDetail> {
  return _json<GroupDetail>(`${GW}/${encodeURIComponent(id)}`);
}
