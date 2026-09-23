DROP TABLE IF EXISTS query_upload;

CREATE TABLE query_upload (
  job_id VARCHAR PRIMARY KEY,
  filename VARCHAR NOT NULL
);
