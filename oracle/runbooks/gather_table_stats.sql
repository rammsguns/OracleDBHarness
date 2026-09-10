-- @id: runbook.gather_table_stats
-- @title: Gather statistics for one table
-- @description: Collects optimizer statistics for a single named table with Oracle
--               default sampling. It changes shared database state and can change
--               execution plans, so it requires an explicit authorized execution
--               action and is not offered in production.
-- @capabilities: dbms_stats
-- @risk: administrative
-- @min_version: 11
-- @parameters: owner, table_name
-- @privileges: ANALYZE ANY, or ownership of the table
-- @returns: none
BEGIN
  DBMS_STATS.GATHER_TABLE_STATS(
    ownname          => :owner,
    tabname          => :table_name,
    estimate_percent => DBMS_STATS.AUTO_SAMPLE_SIZE,
    cascade          => TRUE,
    no_invalidate    => FALSE);
END;
