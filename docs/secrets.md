# Secrets in pigsty-lite

pigsty-lite stores all sensitive values in an ansible-vault encrypted
file at `group_vars/all/vault.yml`. The vault passphrase lives in
`.pigsty/vault-pass` on the control node. Both paths are gitignored.

## Lifecycle

The `./configure` script bootstraps and maintains the vault:

| Run                            | Behavior                                                       |
|--------------------------------|----------------------------------------------------------------|
| First interactive run          | Prompts for a vault passphrase, generates machine secrets, prompts for human secrets, writes encrypted `vault.yml`. |
| Subsequent interactive runs    | Loads existing vault; adds only missing keys (idempotent).      |
| Silent run (`./configure -s`)  | Refuses to prompt. Bootstraps machine secrets only; errors if no passphrase file exists. Add `--no-vault` to skip entirely. |
| `./configure --rotate KEY`     | Force-regenerates one named key in the vault.                   |

## Secret categories

**Machine-to-machine** (generated automatically; never shown to operator):

- `vault_patroni_superuser_password` — Patroni's `postgres` superuser DSN
- `vault_patroni_replication_password` — `replicator` user used for streaming replication
- `vault_patroni_rewind_password` — `rewind_user` used by pg_rewind / leader-recovery checks
- `vault_haproxy_stats_password` — haproxy stats endpoint auth (used only by haproxy itself; no exporter or dashboard consumes it)

**Human-facing** (prompted on first configure):

- `vault_grafana_admin_password` — Grafana admin web UI login

## Where the value comes from at deploy time

Role defaults resolve the password as:

```yaml
grafana_admin_password: >-
  {{ vault_grafana_admin_password
     | mandatory('vault_grafana_admin_password not set; run ./configure to bootstrap the vault') }}
```

Variable precedence (low → high):

1. Role default (the line above) — production path; reads from vault.
2. `group_vars/all/vault.yml` — vault layer; populated by `./configure`.
3. Inventory `group_vars` — test/dev override (e.g., `grafana_admin_password: grafana-test-pw`).

If a test scenario sets the alias var (`grafana_admin_password`) directly,
that wins over the role default — the `vault_*` var is never looked up,
and the `mandatory` filter never executes. That's why tests don't need a
vault file.

If nothing higher provides the value and the vault is missing the key,
the `mandatory` filter raises with the configured message — fail fast.

## Backup

`group_vars/all/vault.yml` and `.pigsty/vault-pass` are **not** in git.
Losing the passphrase is unrecoverable — back both up out of band
(password manager, encrypted USB, etc.).

If the vault file is lost but the passphrase is intact, re-running
`./configure` generates a fresh vault — but this rotates every secret,
which will break an existing running cluster. Plan accordingly.

## Test / CI behavior

Molecule scenarios provide plaintext values in `group_vars.all` of each
`molecule.yml`. Those values shadow the vault-backed defaults, so tests
never need a vault file. CI runs `./configure --no-vault` if it needs
the configure flow at all.

## Rotation example

To rotate the haproxy stats password:

```bash
./configure --rotate vault_haproxy_stats_password
make deploy
```

The next `make deploy` re-renders haproxy config with the new password.
