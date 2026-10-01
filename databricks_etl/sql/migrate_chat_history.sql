-- Additive document chat history. One row per message; payload is the message JSON (question or
-- answer text, tools used, plain-text citations), never document bytes. Rows are read and written
-- by the App only, always filtered by the signed-in user_id.
CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_chat_messages'
) (
  conversation_id STRING NOT NULL, user_id STRING NOT NULL, seq INT NOT NULL,
  role STRING NOT NULL, payload STRING NOT NULL, created_at TIMESTAMP NOT NULL
)
USING DELTA TBLPROPERTIES ('delta.isolationLevel' = 'Serializable');
