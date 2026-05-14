# Role index

pigsty-lite is split into small Ansible roles with narrow ownership. The
site playbook wires them together in phase order, while each role README
documents its variables, files, and operational contract.

| Role | Purpose | README |
| --- | --- | --- |
| `preflight` | Validates target-host prerequisites before any configuration changes: OS family, SELinux mode, firewalld, and storage assumptions. | [roles/preflight/README.md](../roles/preflight/README.md) |
| `repos` | Configures package repositories, including PGDG, optional EPEL, and repo priority behavior. | [roles/repos/README.md](../roles/repos/README.md) |
| `node` | Applies the shared host baseline: hostname, hosts file, sysctl, journald, and baseline firewalld setup. | [roles/node/README.md](../roles/node/README.md) |
| `ca` | Creates or manages the control-node certificate authority used by internal TLS. | [roles/ca/README.md](../roles/ca/README.md) |
| `certs` | Issues and installs per-host certificates signed by the pigsty-lite CA. | [roles/certs/README.md](../roles/certs/README.md) |
| `etcd` | Installs and configures the etcd cluster used as Patroni's distributed consensus store. | [roles/etcd/README.md](../roles/etcd/README.md) |
| `postgres` | Installs PostgreSQL packages, prepares vendor-default filesystem paths, and ensures Patroni owns the server lifecycle. | [roles/postgres/README.md](../roles/postgres/README.md) |
| `patroni` | Configures and runs Patroni for PostgreSQL HA, replication, TLS, bootstrap, and health gates. | [roles/patroni/README.md](../roles/patroni/README.md) |
| `pgbouncer` | Installs and configures local pgBouncer for pooled PostgreSQL client connections. | [roles/pgbouncer/README.md](../roles/pgbouncer/README.md) |
| `haproxy` | Configures local HAProxy routing for primary and replica PostgreSQL endpoints using Patroni health checks. | [roles/haproxy/README.md](../roles/haproxy/README.md) |
| `vip_manager` | Optionally manages a leader-bound virtual IP backed by etcd state. | [roles/vip_manager/README.md](../roles/vip_manager/README.md) |
| `provision` | Applies declarative PostgreSQL HBA rules, roles, databases, extensions, and memberships on the current Patroni leader. | [roles/provision/README.md](../roles/provision/README.md) |
