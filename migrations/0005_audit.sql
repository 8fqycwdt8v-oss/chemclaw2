CREATE TABLE audit_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  table_name  TEXT NOT NULL,
  row_id      UUID NOT NULL,
  operation   TEXT NOT NULL,
  old_data    JSONB,
  new_data    JSONB,
  changed_by  TEXT,
  changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX audit_log_table_row ON audit_log (table_name, row_id);
CREATE INDEX audit_log_changed_at ON audit_log (changed_at DESC);

CREATE OR REPLACE FUNCTION audit_trigger_fn() RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO audit_log (table_name, row_id, operation, old_data, new_data, changed_by)
  VALUES (
    TG_TABLE_NAME,
    COALESCE(NEW.id, OLD.id),
    TG_OP,
    CASE TG_OP WHEN 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
    CASE TG_OP WHEN 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
    current_setting('app.current_user_id', true)
  );
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_compounds
  AFTER INSERT OR UPDATE OR DELETE ON compounds
  FOR EACH ROW EXECUTE FUNCTION audit_trigger_fn();

CREATE TRIGGER audit_reactions
  AFTER INSERT OR UPDATE OR DELETE ON reactions
  FOR EACH ROW EXECUTE FUNCTION audit_trigger_fn();

CREATE TRIGGER audit_wiki_pages
  AFTER INSERT OR UPDATE OR DELETE ON wiki_pages
  FOR EACH ROW EXECUTE FUNCTION audit_trigger_fn();

CREATE TRIGGER audit_synthesis_campaigns
  AFTER INSERT OR UPDATE OR DELETE ON synthesis_campaigns
  FOR EACH ROW EXECUTE FUNCTION audit_trigger_fn();

-- RLS: always-true single-tenant policies; swap USING body for multi-tenant
ALTER TABLE compounds ENABLE ROW LEVEL SECURITY;
CREATE POLICY compounds_allow ON compounds USING (true);

ALTER TABLE reactions ENABLE ROW LEVEL SECURITY;
CREATE POLICY reactions_allow ON reactions USING (true);

ALTER TABLE wiki_pages ENABLE ROW LEVEL SECURITY;
CREATE POLICY wiki_pages_allow ON wiki_pages USING (true);

ALTER TABLE synthesis_campaigns ENABLE ROW LEVEL SECURITY;
CREATE POLICY synthesis_campaigns_allow ON synthesis_campaigns USING (true);

ALTER TABLE campaign_steps ENABLE ROW LEVEL SECURITY;
CREATE POLICY campaign_steps_allow ON campaign_steps USING (true);

ALTER TABLE wiki_chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY wiki_chunks_allow ON wiki_chunks USING (true);

ALTER TABLE wiki_citations ENABLE ROW LEVEL SECURITY;
CREATE POLICY wiki_citations_allow ON wiki_citations USING (true);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_log_allow ON audit_log USING (true);
