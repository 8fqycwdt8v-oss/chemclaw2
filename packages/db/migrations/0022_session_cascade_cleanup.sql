-- v2.1-A1: cascade agent_feedback and agent_overrides on agent_sessions delete.
--
-- agent_sessions has a composite PK (project_key, session_id, subpath), so a
-- plain FK from agent_feedback.session_id is not possible without widening the
-- referenced columns. A trigger that fires on the main-key delete (subpath = '')
-- removes the related feedback and overrides rows. This matches the spirit of
-- ON DELETE CASCADE without restructuring either table.
--
-- Why DELETE-trigger and not FK: agent_feedback / agent_overrides only carry
-- session_id (the SDK session id is a UUID, unique in practice across
-- project_keys), and adding project_key + subpath to those tables just to
-- satisfy a composite FK is gratuitous.
CREATE OR REPLACE FUNCTION cascade_session_audit_rows() RETURNS TRIGGER AS $$
BEGIN
  DELETE FROM agent_feedback WHERE session_id = OLD.session_id;
  -- agent_overrides is intentionally NOT swept. Migration 0016 deliberately
  -- omits any UPDATE/DELETE policy on agent_overrides, making it append-only
  -- for compliance evidence (a scheduled-substance gate override must persist
  -- past the session that triggered it). The trigger runs with table-owner
  -- privilege and would otherwise bypass that intent — leave overrides alone.
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS cascade_agent_session_audit ON agent_sessions;
CREATE TRIGGER cascade_agent_session_audit
  AFTER DELETE ON agent_sessions
  FOR EACH ROW
  WHEN (OLD.subpath = '')
  EXECUTE FUNCTION cascade_session_audit_rows();
