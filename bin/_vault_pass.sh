#!/bin/sh
# Vault password source for ansible.cfg.
# Prints the passphrase from .pigsty/vault-pass if it exists; otherwise
# prints a placeholder so ansible doesn't error on load when nothing is
# encrypted (lint, fresh clone, CI without vault). Real decryption fails
# loudly anyway if vault.yml exists but the wrong passphrase is supplied.
PASS_FILE="$(dirname "$0")/../.pigsty/vault-pass"
if [ -f "$PASS_FILE" ]; then
    cat "$PASS_FILE"
else
    echo "pigsty-lite-no-vault-placeholder"
fi
exit 0
