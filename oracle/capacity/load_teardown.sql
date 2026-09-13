-- Removes the capacity run's marker table (see load_schema.sql). Run as the schema owner
-- on each load database once capacity testing is finished.

DROP TABLE harness_load_markers PURGE;
