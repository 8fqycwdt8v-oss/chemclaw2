-- Followup #3: agent_sessions and rate_limits were missing RLS. Every other
-- table has ENABLE ROW LEVEL SECURITY + a stub permissive policy (see 0005,
-- 0012, 0014, 0016, 0018, 0019). These two are the most sensitive: sessions
-- holds the full LLM transcript JSONB, rate_limits keys include userId. If
-- RLS is ever flipped from stub to per-tenant predicates these two will
-- silently stay wide open without this migration.
ALTER TABLE agent_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_sessions_all ON agent_sessions FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE rate_limits ENABLE ROW LEVEL SECURITY;
CREATE POLICY rate_limits_all ON rate_limits FOR ALL USING (true) WITH CHECK (true);

-- Followup #2/#4/#12: audit_log.changed_by was always NULL because the
-- audit trigger read current_setting('app.current_user_id') but no caller
-- ever set it (withUserContext was exported but never invoked). Replace the
-- GUC-based lookup with direct extraction from the row's own created_by /
-- updated_by columns — every audited table (compounds, reactions,
-- wiki_pages, synthesis_campaigns) carries at least created_by.
CREATE OR REPLACE FUNCTION audit_trigger_fn() RETURNS TRIGGER AS $$
DECLARE
  new_json JSONB := CASE TG_OP WHEN 'DELETE' THEN NULL ELSE to_jsonb(NEW) END;
  old_json JSONB := CASE TG_OP WHEN 'INSERT' THEN NULL ELSE to_jsonb(OLD) END;
  who      TEXT;
BEGIN
  -- Prefer updated_by on UPDATE/DELETE, fall back to created_by, then to the
  -- GUC for backwards compatibility with any callers that do set it.
  who := CASE
    WHEN TG_OP = 'INSERT' THEN new_json->>'created_by'
    WHEN TG_OP = 'UPDATE' THEN COALESCE(new_json->>'updated_by', new_json->>'created_by')
    WHEN TG_OP = 'DELETE' THEN COALESCE(old_json->>'updated_by', old_json->>'created_by')
  END;
  who := COALESCE(who, current_setting('app.current_user_id', true));

  INSERT INTO audit_log (table_name, row_id, operation, old_data, new_data, changed_by)
  VALUES (TG_TABLE_NAME, COALESCE(NEW.id, OLD.id), TG_OP, old_json, new_json, who);
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
