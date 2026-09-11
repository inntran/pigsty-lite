"""The replication-lag health check must not crash on a non-integer lag.

Patroni reports `lag` as an integer, but substitutes the string "unknown"
when a replica cannot compute it. Comparing that against an int raises a
Jinja TypeError, which would abort a switchover with a type error instead
of the intended failure message.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
LAG_THRESHOLD = 1048576


def _lag_expression() -> str:
    """Pull the lag-selection expression out of the role, so this test tracks
    the real task rather than a copy of it."""
    tasks = yaml.safe_load((ROOT / "roles/cluster_ops/tasks/assert_healthy.yml").read_text())
    for task in tasks:
        if "ansible.builtin.set_fact" in task:
            fact = task["ansible.builtin.set_fact"]
            if "cluster_ops_lagging_replicas" in fact:
                return fact["cluster_ops_lagging_replicas"]
    raise AssertionError("cluster_ops_lagging_replicas set_fact not found")


def _evaluate(members: list[dict]) -> list[str]:
    env = Environment()
    # Ansible's `integer` test rejects bools; Jinja has no built-in equivalent.
    env.tests["integer"] = lambda v: isinstance(v, int) and not isinstance(v, bool)

    expression = _lag_expression()
    # The role renders a threshold var through `| int`; substitute the literal.
    expression = re.sub(
        r"cluster_ops_replication_lag_max_bytes \| int", str(LAG_THRESHOLD), expression
    )
    view = {"json": {"members": members}}
    rendered = env.from_string(expression).render(cluster_ops_health_view=view)
    return re.findall(r"'name': '([^']+)'", rendered)


def test_unknown_lag_is_flagged_rather_than_raising():
    flagged = _evaluate(
        [
            {"name": "r1", "role": "replica", "state": "running", "lag": "unknown"},
            {"name": "r2", "role": "replica", "state": "running", "lag": 0},
        ]
    )
    assert flagged == ["r1"]


def test_healthy_replicas_are_not_flagged():
    flagged = _evaluate(
        [
            {"name": "r1", "role": "replica", "state": "running", "lag": 0},
            {"name": "r2", "role": "replica", "state": "running", "lag": 1000},
        ]
    )
    assert flagged == []


def test_replica_over_threshold_is_flagged():
    flagged = _evaluate(
        [{"name": "r1", "role": "replica", "state": "running", "lag": LAG_THRESHOLD + 1}]
    )
    assert flagged == ["r1"]


def test_leader_lag_is_ignored():
    flagged = _evaluate(
        [
            {"name": "leader01", "role": "leader", "state": "running", "lag": "unknown"},
            {"name": "r1", "role": "replica", "state": "running", "lag": 0},
        ]
    )
    assert flagged == []


@pytest.mark.parametrize("lag", ["unknown", None, "1048577"])
def test_non_integer_lag_never_raises(lag):
    """Any non-integer lag counts as unhealthy instead of blowing up."""
    assert _evaluate([{"name": "r1", "role": "replica", "state": "running", "lag": lag}]) == ["r1"]
