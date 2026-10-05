"""Grafana dashboards may only query metrics pigsty-lite actually collects.

The dashboards in roles/grafana/files/dashboards/ are adapted from Pigsty,
whose metric names (pg_exporter), recording rules (`pg:ins:*`) and labels
(`cls`, `ins`, `ip`) do not exist here. A leftover name renders as an empty
panel with no error, so nothing else would catch it.

The fixture is a snapshot of metric names from an AIO deployment. Passing
here proves only that each name exists, not that a query returns data; the
live check in roles/grafana/README.md covers that.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = ROOT / "roles/grafana/files/dashboards"
FIXTURE = ROOT / "tests/configure/fixtures/grafana_metric_names.txt"

_VARIABLE = re.compile(r"\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*|\[\[[^\]]+\]\]")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')
_IDENTIFIER = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_PROMQL_KEYWORDS = {
    "and",
    "bool",
    "by",
    "group_left",
    "group_right",
    "ignoring",
    "on",
    "offset",
    "or",
    "unless",
    "without",
}


def _label_values_first_argument(expr: str) -> tuple[str, bool] | None:
    match = re.search(r"\blabel_values\s*\(", expr, re.IGNORECASE)
    if not match:
        return None

    opening = match.end() - 1
    depth = 1
    in_braces = 0
    in_brackets = 0
    quote = None
    escaped = False
    for index in range(opening + 1, len(expr)):
        char = expr[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'", "`"):
            quote = char
        elif char == "{":
            in_braces += 1
        elif char == "}":
            in_braces -= 1
        elif char == "[":
            in_brackets += 1
        elif char == "]":
            in_brackets -= 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return expr[opening + 1 : index].strip(), False
        elif char == "," and depth == 1 and in_braces == 0 and in_brackets == 0:
            return expr[opening + 1 : index].strip(), True
    return expr[opening + 1 :].strip(), False


def _extract_metric_names(expr: str) -> set[str]:
    expr = _VARIABLE.sub(" 0 ", expr)
    names = set()
    selectors = re.findall(r"\{([^{}]*)\}", expr)
    for selector in selectors:
        match = re.search(
            r"(?:^|,)\s*__name__\s*=(?!=|~)\s*(['\"])([a-zA-Z_:][a-zA-Z0-9_:]*)\1"
            r"\s*(?=,|$)",
            selector,
        )
        if match:
            names.add(match.group(2))

    expr = _STRING.sub(" ", expr)
    expr = re.sub(r"\{[^{}]*\}", " ", expr)
    expr = re.sub(r"\[[^\]]*\]", " ", expr)
    expr = re.sub(
        r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^()]*\)",
        " ",
        expr,
        flags=re.IGNORECASE,
    )
    expr = re.sub(
        r"(?<![A-Za-z0-9_:])[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
        r"(?:[eE][+-]?\d+)?(?:ms|s|m|h|d|w|y)?(?![A-Za-z0-9_:])",
        " ",
        expr,
        flags=re.IGNORECASE,
    )

    for match in _IDENTIFIER.finditer(expr):
        name = match.group()
        if re.match(r"\s*\(", expr[match.end() :]):
            continue
        if name.lower() in _PROMQL_KEYWORDS or name.lower() in {"inf", "nan"}:
            continue
        names.add(name)
    return names


def metric_names(expr: str) -> set[str]:
    """Extract metric selectors from a PromQL or MetricsQL expression."""
    label_values = _label_values_first_argument(expr)
    if label_values:
        first_argument, has_label_argument = label_values
        if has_label_argument or "{" in first_argument:
            return _extract_metric_names(first_argument)
        return set()
    return _extract_metric_names(expr)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (
            'sum by (cluster) (rate(pg_stat_database_xact_commit{datname=~"$d"}[5m]))',
            {"pg_stat_database_xact_commit"},
        ),
        ("a / on (instance) group_left (job) b", {"a", "b"}),
        ('label_values(up{job="postgres"}, cluster)', {"up"}),
        ("label_values(cluster)", set()),
        ("topk($n, foo)", {"foo"}),
        (
            "histogram_quantile(0.99, sum(rate(x_bucket[$__rate_interval])) by (le))",
            {"x_bucket"},
        ),
        ("clamp_min(node_load1 offset 1h, 0) > bool 1", {"node_load1"}),
        ('topk(5, foo{a="b(c)"})', {"foo"}),
        (
            '1 - avg(irate(node_cpu_seconds_total{mode="idle"}[1m]))',
            {"node_cpu_seconds_total"},
        ),
    ],
)
def test_metric_names_extracts_promql_metrics(expr: str, expected: set[str]):
    assert metric_names(expr) == expected, f"{expr!r}: expected {expected!r}"


def _dashboard_data():
    for path in sorted(DASHBOARDS.glob("*.json")):
        try:
            dashboard = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            pytest.fail(f"{path.name}: invalid JSON: {error}")
        yield path, dashboard


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _query_texts(dashboard):
    for item in _walk(dashboard):
        if isinstance(item.get("expr"), str):
            yield item["expr"]

    templating = dashboard.get("templating", {})
    for variable in templating.get("list", []):
        if variable.get("type") != "query":
            continue
        query = variable.get("query")
        if isinstance(query, str):
            yield query
        elif isinstance(query, dict) and isinstance(query.get("query"), str):
            yield query["query"]


def _datasources(dashboard):
    for item in _walk(dashboard):
        if "datasource" in item:
            yield item["datasource"]


def _dashboard_texts(dashboard):
    for item in _walk(dashboard):
        if isinstance(item.get("title"), str):
            yield item["title"]
    if isinstance(dashboard.get("description"), str):
        yield dashboard["description"]


def _fixture_metric_names() -> set[str]:
    return {
        line.strip()
        for line in FIXTURE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_dashboards_are_valid_have_unique_uids_and_null_ids():
    dashboards = list(_dashboard_data())
    uids = {}
    for path, dashboard in dashboards:
        assert isinstance(dashboard, dict), f"{path.name}: dashboard root is not an object"
        uid = dashboard.get("uid")
        assert isinstance(uid, str) and uid.strip(), f"{path.name}: invalid uid {uid!r}"
        assert uid not in uids, f"{path.name}: duplicate uid {uid!r} also in {uids.get(uid)}"
        uids[uid] = path.name
        assert dashboard.get("id") is None, f"{path.name}: id must be null or absent"


def test_datasources_use_the_victoriametrics_plugin_or_grafana_builtins():
    expected = {"type": "victoriametrics-metrics-datasource", "uid": "VictoriaMetrics"}
    for path, dashboard in _dashboard_data():
        for datasource in _datasources(dashboard):
            if isinstance(datasource, dict):
                valid = datasource == expected or datasource.get("type") == "grafana"
                assert valid, f"{path.name}: invalid datasource object {datasource!r}"
            elif datasource is not None:
                assert datasource in {"-- Grafana --", "-- Mixed --"}, (
                    f"{path.name}: invalid datasource {datasource!r}"
                )


def test_queries_do_not_use_legacy_labels():
    matcher = re.compile(r"\b(cls|ins|ip)\s*(=~|!~|!=|=)")
    grouping = re.compile(
        r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\(([^)]*)\)",
        re.IGNORECASE,
    )
    labels = re.compile(r"\b(?:cls|ins|ip)\b")
    for path, dashboard in _dashboard_data():
        for query in _query_texts(dashboard):
            grouped_labels = [
                label for clause in grouping.findall(query) for label in labels.findall(clause)
            ]
            assert not matcher.search(query) and not grouped_labels, (
                f"{path.name}: legacy label in query {query!r}"
            )


def test_queries_do_not_use_recording_rule_metric_names():
    for path, dashboard in _dashboard_data():
        for query in _query_texts(dashboard):
            colon_names = sorted(name for name in metric_names(query) if ":" in name)
            assert not colon_names, (
                f"{path.name}: recording-rule metrics {colon_names!r} in {query!r}"
            )


def test_queries_only_use_metrics_in_fixture():
    known = _fixture_metric_names()
    missing_by_file = {}
    for path, dashboard in _dashboard_data():
        missing = sorted(
            {
                name
                for query in _query_texts(dashboard)
                for name in metric_names(query)
                if name not in known
            }
        )
        if missing:
            missing_by_file[path.name] = missing
    assert not missing_by_file, f"metrics missing from {FIXTURE.name}: {missing_by_file!r}"


def test_dashboard_titles_links_and_description_do_not_use_pgsql():
    for path, dashboard in _dashboard_data():
        offending = [text for text in _dashboard_texts(dashboard) if "PGSQL" in text]
        assert not offending, f"{path.name}: forbidden title/description values {offending!r}"


def _link_urls(dashboard):
    for item in _walk(dashboard):
        if isinstance(item.get("url"), str):
            yield item["url"]


def test_dashboard_links_target_shipped_dashboards_with_native_variables():
    dashboards = list(_dashboard_data())
    shipped = {dashboard["uid"] for _, dashboard in dashboards}
    legacy_variable = re.compile(r"var-(?:cls|ins|ip)=|\$\{?(?:cls|ins|ip)\b")
    for path, dashboard in dashboards:
        for url in _link_urls(dashboard):
            target = re.match(r"/d/([^/?#]+)", url)
            assert not target or target.group(1) in shipped, (
                f"{path.name}: link to unshipped dashboard {url!r}"
            )
            assert not legacy_variable.search(url), f"{path.name}: legacy variable in {url!r}"


def test_queries_do_not_select_metric_names_by_regex():
    # metric_names() cannot resolve {__name__=~"..."}, so the fixture check
    # would silently pass such a query. Forbid the form instead.
    regex_name = re.compile(r"__name__\s*(=~|!~)")
    for path, dashboard in _dashboard_data():
        for query in _query_texts(dashboard):
            assert not regex_name.search(query), (
                f"{path.name}: regex __name__ matcher defeats the fixture check: {query!r}"
            )
