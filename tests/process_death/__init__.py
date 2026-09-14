"""Killing a real API process at a chosen moment, and restarting over what it left.

``tests/integration/test_restart.py`` abandons an application inside the test process.
That leaves the metadata store as a dead process would, but the Oracle connection is
still alive in the same interpreter, so it says nothing about what the *database* does
when the process holding a session goes away. This package does the real thing:

* ``child.py`` runs the API in a separate interpreter. It can be told to stop at one
  point - after an execution record is written but before dispatch, after a statement
  has returned from the driver, before a COMMIT is sent, or after one has returned - and
  wait there. The pause is a synchronisation point, not a fault: nothing is raised,
  nothing is faked, and the database is left exactly as the statement left it.
* ``harness.py`` starts that process, waits for it to reach the point, and ends it with
  ``Popen.kill()``: ``SIGKILL`` on POSIX, ``TerminateProcess`` on Windows. No shutdown
  hook, ``finally`` block or atexit handler in the child runs. The operating system
  closes its sockets, and the database cleans up the session on its own schedule.
* An *observer* reads the database through its own connection, never through the API,
  and waits for that cleanup before trusting what it reads.

The distinction matters for what may be recorded as evidence. A fault hook such as the
stand-in's ``fail_next_commit`` proves the harness classifies an error; it proves nothing
about recovery after process death, and must never be recorded as such. Only a run in
which the child's own backend reports ``oracledb`` and the observer is an independent
Oracle session is written to the qualification report.
"""
