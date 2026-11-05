DROP TABLE IF EXISTS query_upload;

-- One row per query upload this instance kept (#40): the name the file was uploaded
-- under, and since the profile lists a user's uploads, the local id of the account that
-- uploaded it. The id and not the username: a username is renamed and freed here
-- (ADR-0007), the id is not. NULL on rows from before the column existed.
CREATE TABLE query_upload (
  job_id VARCHAR PRIMARY KEY,
  filename VARCHAR NOT NULL,
  user_id INTEGER
);
