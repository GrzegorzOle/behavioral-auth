"""Cycle history has to survive a restart.

Every promotion gate but one is derived from the database: sequences, active
minutes, distinct hours. The cycle state was held only in memory, and it fails
in three separate ways when it starts from zero after a restart — the streak
resets so a frequently-rebooted machine never promotes, `prev_shape` is absent
so the threshold-drift gate passes for free, and `seq_at_last_cycle` is zero so
a cycle fires immediately over data already judged.

Two of those made promotion *easier*, which is why restoring the streak alone
would have been the wrong fix.
"""

import json

from behavioral_auth.daemon.learning import LearningController


def _record_cycle(conn, enrollment_id, *, cycle_no, streak, stable,
                  n_train=300, n_holdout=80, shape=4.2):
    conn.execute(
        'INSERT INTO learning_cycles (enrollment_id, cycle_no, n_train, n_holdout, '
        'pass_rate, error_ratio, threshold, threshold_drift, separation, stable, '
        'stable_streak, promoted, metrics_json) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [enrollment_id, cycle_no, n_train, n_holdout, 1.0, 0.9, 3.0, 0.0, 2.5,
         stable, streak, False, json.dumps({'shape': shape})])


def test_a_fresh_controller_knows_nothing(cfg):
    lc = LearningController(cfg)
    assert lc.cycle_no == 0
    assert lc.stable_streak == 0
    assert lc.prev_shape is None
    assert lc.seq_at_last_cycle == 0


def test_resume_restores_the_streak_and_both_gates_it_had_disabled(cfg, conn):
    eid = '11111111-1111-1111-1111-111111111111'
    _record_cycle(conn, eid, cycle_no=1, streak=1, stable=True, shape=3.1)
    _record_cycle(conn, eid, cycle_no=2, streak=2, stable=True,
                  n_train=420, n_holdout=105, shape=4.7)

    lc = LearningController(cfg)
    lc.resume(conn, eid)

    assert lc.cycle_no == 2
    assert lc.stable_streak == 2                 # the gate everyone notices
    assert lc.prev_shape == 4.7                  # drift can be computed again
    assert lc.seq_at_last_cycle == 525           # 420 + 105, so 'new' means new


def test_a_broken_streak_is_resumed_as_broken(cfg, conn):
    """Resuming must not launder an unstable cycle into a streak."""
    eid = '22222222-2222-2222-2222-222222222222'
    _record_cycle(conn, eid, cycle_no=1, streak=1, stable=True)
    _record_cycle(conn, eid, cycle_no=2, streak=0, stable=False)

    lc = LearningController(cfg)
    lc.resume(conn, eid)
    assert lc.stable_streak == 0


def test_another_enrollments_history_is_not_inherited(cfg, conn):
    """`reset` retires the enrollment and starts a new one. If resume crossed
    enrollments, a reset would keep the streak it was meant to destroy."""
    old = '33333333-3333-3333-3333-333333333333'
    new = '44444444-4444-4444-4444-444444444444'
    _record_cycle(conn, old, cycle_no=7, streak=3, stable=True)

    lc = LearningController(cfg)
    lc.resume(conn, new)

    assert lc.cycle_no == 0
    assert lc.stable_streak == 0
    assert lc.prev_shape is None
    assert lc.seq_at_last_cycle == 0


def test_unreadable_metrics_do_not_break_the_resume(cfg, conn):
    eid = '55555555-5555-5555-5555-555555555555'
    conn.execute(
        'INSERT INTO learning_cycles (enrollment_id, cycle_no, n_train, n_holdout, '
        'stable, stable_streak, promoted, metrics_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [eid, 4, 200, 50, True, 2, False, None])

    lc = LearningController(cfg)
    lc.resume(conn, eid)

    assert lc.stable_streak == 2
    assert lc.prev_shape is None
    assert lc.seq_at_last_cycle == 250
