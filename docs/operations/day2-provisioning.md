# Day-2 provisioning

All provisioning is declarative. Edit `responses/site.rsp.yml`, then
`make deploy`. The `provision` role runs once per deploy on whichever
host is the current Patroni leader; replicas pick up the changes via
streaming replication. To target only the provisioning step, pass
`--tags provision`.

## Add a database

```yaml
postgres:
  databases:
    - { name: app,       owner: app }
    - { name: analytics, owner: analytics }   # added
```

```bash
make deploy           # full deploy
# or:
ansible-playbook playbooks/site.yml --tags provision,databases
```

## Add a user

```yaml
postgres:
  users:
    - name: app
      password: !vault |
        $ANSIBLE_VAULT;1.1;AES256
        ...
      roles: [pg_read_all_data]
    - name: analytics                     # added
      password: !vault | ...
      roles: [pg_write_all_data]
```

The vault password file must be available (`ANSIBLE_VAULT_PASSWORD_FILE`
or `--ask-vault-pass`).

## Add an extension

```yaml
postgres:
  extensions:
    - pg_stat_statements
    - { name: pgvector, db: app }   # in app db only, not postgres
```

The extension package itself must already be installed on the host. PG
extensions ship as `postgresql<ver>-contrib` (in PGDG) or as separate
RPMs. If the extension is missing at the OS level, `make deploy` fails
on the `postgresql_ext` task with a clear error from PostgreSQL.

## Open HBA to a new CIDR

```yaml
postgres:
  hba_rules:
    - { db: app, user: app, source: 10.20.40.0/24, method: scram-sha-256 }
    - { db: app, user: app, source: 10.20.41.0/24, method: scram-sha-256 } # added
```

The `provision` role rewrites `pg_hba.conf` and signals
`pg_reload_conf()`. No restart, no client disruption.

## Common gotchas

- **Extension missing at OS level**: install the RPM (`dnf install postgresql18-contrib`) and re-deploy.
- **Vault password unavailable**: `make deploy` fails at variable templating with a clear error before any task runs.
- **Hand-edited `pg_hba.conf` reverts**: that's intentional. The role owns the file.
- **Per-table grants**: out of scope. Issue them by hand or via a future migration tool; we won't manage them.
