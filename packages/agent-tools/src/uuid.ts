// Wave-3f cut: UUID regex was duplicated in 11 apps/web routes plus a couple
// of tool factories (lookup-properties, register-property). agent-tools is
// the lowest-level package both apps/web and the workers depend on, so this
// is the right home — mirrors slug.ts.
//
// Matches RFC 4122 v1-v5 canonical form (8-4-4-4-12 hex, case-insensitive).
// Not anchored to v4 because Clerk's user ids aren't UUIDs anyway; this
// regex is only used for the agent's own DB row identifiers (sessions,
// campaigns, properties, etc.) which Postgres generates via gen_random_uuid().
export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const isUuid = (v: unknown): v is string => typeof v === 'string' && UUID_RE.test(v);
