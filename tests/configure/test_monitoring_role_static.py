"""Static checks for monitoring role behavior."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _walk_tasks(tasks):
    """Yield tasks including those nested in block/rescue/always."""
    for task in tasks or []:
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _walk_tasks(task[key])
        yield task


def _load_yaml(path: str):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def test_monitoring_server_main_imports_existing_task_files():
    tasks = _load_yaml("roles/monitoring_server/tasks/main.yml")
    imported = [
        task["ansible.builtin.import_tasks"]
        for task in tasks
        if "ansible.builtin.import_tasks" in task
    ]

    assert imported
    for relative_path in imported:
        assert (ROOT / "roles/monitoring_server/tasks" / relative_path).exists()


def test_monitoring_agents_remote_write_defaults_use_http_scheme():
    defaults = _load_yaml("roles/monitoring_agents/defaults/main.yml")

    assert defaults["monitoring_agents_vmagent_remote_write_url"].startswith("http://")
    assert defaults["monitoring_agents_vlagent_remote_write_url"].startswith("http://")


def test_monitoring_server_does_not_pass_recursive_collection_vars():
    tasks = _load_yaml("roles/monitoring_server/tasks/main.yml")
    text = yaml.safe_dump(tasks)

    assert "{{ victoriametrics_service_args" not in text
    assert "{{ victorialogs_service_args" not in text


def test_monitoring_server_creates_alertmanager_identity_before_data_dir():
    tasks = _load_yaml("roles/monitoring_server/tasks/_alertmanager.yml")
    group_index = next(index for index, task in enumerate(tasks) if "ansible.builtin.group" in task)
    user_index = next(index for index, task in enumerate(tasks) if "ansible.builtin.user" in task)
    data_dir_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.file", {}).get("path")
        == "{{ monitoring_server_alertmanager_data_dir }}"
    )

    assert group_index < user_index < data_dir_index


def test_monitoring_epel_packages_explicitly_enable_epel_repo():
    alertmanager_tasks = _load_yaml("roles/monitoring_server/tasks/_alertmanager.yml")
    alertmanager_install = next(
        task for task in alertmanager_tasks if task.get("name") == "Install Alertmanager"
    )["ansible.builtin.dnf"]

    assert alertmanager_install["enablerepo"] == "{{ epel_repo_id }}"


def test_exporters_install_from_pinned_tarballs_not_dnf():
    """EPEL carries none of the four exporters for EL10, so a dnf install
    would fail at deploy time. They come from pinned release tarballs."""
    exporter_tasks = _load_yaml("roles/monitoring_agents/tasks/_exporters.yml")

    assert not [task for task in exporter_tasks if "ansible.builtin.dnf" in task], (
        "_exporters.yml installs with dnf; no EL10 repo packages these exporters"
    )

    includes = [
        task
        for task in exporter_tasks
        if task.get("ansible.builtin.include_tasks") == "_install_exporter.yml"
    ]
    assert len(includes) == 2, "expected node_exporter plus the PG-side loop"


def test_exporter_install_verifies_a_checksum():
    """An unverified download would install whatever the network returned."""
    tasks = _load_yaml("roles/monitoring_agents/tasks/_install_exporter.yml")
    downloads = [task for task in _walk_tasks(tasks) if "ansible.builtin.get_url" in task]
    assert downloads, "expected a get_url task"
    for task in downloads:
        checksum = task["ansible.builtin.get_url"].get("checksum", "")
        assert checksum.startswith("sha256:"), "the tarball must be checksum-verified"


def _render_scrape_config(**overrides) -> str:
    from jinja2 import Environment

    context = {
        "ansible_managed": "test",
        "monitoring_agents_scrape_interval": "15s",
        "network_loopback_address": "127.0.0.1",
        "cluster_name": "pigsty-lite-test",
        "inventory_hostname": "pgnode01",
        "groups": {"postgres": ["pgnode01"]},
        "monitoring_agents_node_exporter_port": 9100,
        "monitoring_agents_postgres_exporter_port": 9187,
        "monitoring_agents_pgbouncer_exporter_port": 9127,
        "monitoring_agents_pgbackrest_exporter_port": 9854,
        "monitoring_agents_patroni_rest_port": 8008,
        "monitoring_agents_haproxy_stats_port": 9101,
        "monitoring_agents_ca_file": "/etc/pki/pigsty/ca.crt",
    }
    context.update(overrides)
    template = Environment(trim_blocks=False, lstrip_blocks=False).from_string(
        (ROOT / "roles/monitoring_agents/templates/vmagent-scrape.yml.j2").read_text()
    )
    return template.render(**context)


def test_scrape_config_covers_the_four_exporters_on_a_postgres_host():
    jobs = yaml.safe_load(_render_scrape_config())["scrape_configs"]
    names = [job["job_name"] for job in jobs]

    assert {"node", "postgres", "pgbouncer", "pgbackrest"} <= set(names)


def test_spof_does_not_scrape_haproxy():
    """site.yml skips _haproxy.yml on spof, so there is nothing listening.

    Rendering the job anyway parks a permanently-down target in vmsingle for
    the life of the deployment.
    """
    ha = yaml.safe_load(_render_scrape_config(cluster_profile="ha"))
    spof = yaml.safe_load(_render_scrape_config(cluster_profile="spof"))
    aio = yaml.safe_load(_render_scrape_config(cluster_profile="aio"))

    assert "haproxy" in [job["job_name"] for job in ha["scrape_configs"]]
    assert "haproxy" not in [job["job_name"] for job in spof["scrape_configs"]]
    assert "haproxy" not in [job["job_name"] for job in aio["scrape_configs"]]


def test_agents_read_the_ca_from_a_readable_copy_not_the_pki_dir():
    """vmagent/vlagent run as vic_vm_agent/vic_vl_agent.

    `{{ pigsty_pki_dir }}` is 0750 root:pigsty because it holds private keys,
    so pointing the agents at the CA in place yields "cannot read `ca_file`:
    permission denied" -- which takes down the patroni scrape and
    remote_write both, since vmagent builds the TLS transport even for a
    plain-http remote write URL.
    """
    defaults = _load_yaml("roles/monitoring_agents/defaults/main.yml")

    assert defaults["monitoring_agents_ca_source"] == "{{ pigsty_pki_dir }}/ca.crt"
    assert "pigsty_pki_dir" not in defaults["monitoring_agents_ca_file"]

    tasks = list(_walk_tasks(_load_yaml("roles/monitoring_agents/tasks/main.yml")))
    copy = next(
        task
        for task in tasks
        if task.get("name") == "Copy the CA certificate out of the PKI directory"
    )

    assert copy["ansible.builtin.copy"]["src"] == "{{ monitoring_agents_ca_source }}"
    assert copy["ansible.builtin.copy"]["dest"] == "{{ monitoring_agents_ca_file }}"
    assert copy["ansible.builtin.copy"]["mode"] == "0644"
    # A rotated CA is a new file behind an unchanged ExecStart, so the
    # upstream roles see nothing to restart.
    assert set(copy["notify"]) == {"Restart vmagent", "Restart vlagent"}

    handlers = [
        handler["name"] for handler in _load_yaml("roles/monitoring_agents/handlers/main.yml")
    ]
    assert {"Restart vmagent", "Restart vlagent"} <= set(handlers)


def test_pgbouncer_exporter_dsn_points_at_pgbouncers_own_socket_dir():
    """roles/pgbouncer puts the socket under /run/pgbouncer.

    PostgreSQL's socket directory (/run/postgresql) holds no pgBouncer
    socket, so an exporter pointed there logs "connect: no such file or
    directory" every scrape and publishes pgbouncer_up 0 forever -- while
    still serving a healthy-looking /metrics.
    """
    defaults = _load_yaml("roles/monitoring_agents/defaults/main.yml")
    dsn = defaults["monitoring_agents_pgbouncer_exporter_dsn"]

    assert "pgbouncer_unix_socket_dir" in dsn
    assert "/run/postgresql" not in dsn


def test_pgbouncer_exporter_logs_in_by_peer():
    """The exporter reaches the pgBouncer console by peer, with no password.

    Peer auth requires the requested user to equal the connecting OS user, so
    the DSN user, the unit's User= and roles/pgbouncer's hba peer rule must all
    be postgres_osdba, and pgBouncer must run with auth_type = hba.
    """
    unit = (ROOT / "roles/monitoring_agents/templates/pgbouncer-exporter.service.j2").read_text()
    dsn = _load_yaml("roles/monitoring_agents/defaults/main.yml")[
        "monitoring_agents_pgbouncer_exporter_dsn"
    ]

    assert "user={{ postgres_osdba | default('postgres') }}" in dsn
    assert "password" not in dsn
    assert "PGPASSFILE" not in unit
    assert "password" not in unit
    assert "User={{ postgres_osdba | default('postgres') }}" in unit
    assert not (ROOT / "roles/monitoring_agents/templates/pgbouncer-exporter.pgpass.j2").exists()

    exporter_tasks = list(_walk_tasks(_load_yaml("roles/monitoring_agents/tasks/_exporters.yml")))
    cleanup = next(
        task
        for task in exporter_tasks
        if task.get("name") == "Remove the legacy pgbouncer_exporter pgpass file"
    )

    assert cleanup["ansible.builtin.file"]["state"] == "absent"
    pgbouncer_defaults = _load_yaml("roles/pgbouncer/defaults/main.yml")
    assert pgbouncer_defaults["pgbouncer_auth_type"] == "hba"
    assert (
        pgbouncer_defaults["pgbouncer_peer_console_user"]
        == "{{ postgres_osdba | default('postgres') }}"
    )
