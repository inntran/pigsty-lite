# pigsty-lite operator entry points.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

include Makefile.d/lint.mk

.PHONY: help init configure plan deploy clean test-role molecule-image switchover failover minor-upgrade scale-add-replica scale-remove-replica

help:
	@echo "pigsty-lite - operator commands"
	@echo
	@echo "  make init          Set up the control node (install Galaxy collections + roles)"
	@echo "  make configure     Interactive wizard; emits inventory + response file"
	@echo "  make plan          Run site.yml in --check --diff mode"
	@echo "  make deploy        Run site.yml against the active inventory"
	@echo "  make lint          Run all linters"
	@echo "  make molecule-image Build/reuse local shared Molecule base image"
	@echo "  make test-role ROLE=<name>   Run molecule for a single role"
	@echo "  make clean         Remove generated artifacts"
	@echo
	@echo "  Lifecycle operations:"
	@echo "  make switchover                     Controlled primary switchover"
	@echo "  make failover CANDIDATE=<host>      Manual failover to a named candidate"
	@echo "  make minor-upgrade                  Rolling minor PostgreSQL upgrade"
	@echo "  make scale-add-replica HOST=<host>  Add a replica (host must be in inventory)"
	@echo "  make scale-remove-replica HOST=<host>  Decommission a replica"

init:
	ansible-galaxy collection install -r requirements.yml -p ./collections
	ansible-galaxy role install -r requirements.yml -p ./roles.galaxy

configure:
	./configure

plan: init
	ansible-playbook playbooks/site.yml --check --diff

deploy: init
	ansible-playbook playbooks/site.yml

molecule-image:
	./bin/molecule_image.sh tests/molecule/Containerfile localhost/molecule-base

test-role: molecule-image
	@if [ -z "$(ROLE)" ]; then echo "Usage: make test-role ROLE=<name>"; exit 2; fi
	cd tests/molecule/$(ROLE) && molecule test

clean:
	rm -rf .ansible/facts dist/ artifacts/
	find . -name __pycache__ -type d -exec rm -rf {} +

switchover:
	ansible-playbook playbooks/switchover.yml

failover:
	@if [ -z "$(CANDIDATE)" ]; then echo "Usage: make failover CANDIDATE=<host>"; exit 2; fi
	ansible-playbook playbooks/failover.yml -e candidate=$(CANDIDATE)

minor-upgrade:
	ansible-playbook playbooks/minor_upgrade.yml

scale-add-replica:
	@if [ -z "$(HOST)" ]; then echo "Usage: make scale-add-replica HOST=<host>"; exit 2; fi
	ansible-playbook playbooks/scale_add_replica.yml -e target_host=$(HOST) --limit $(HOST),postgres

scale-remove-replica:
	@if [ -z "$(HOST)" ]; then echo "Usage: make scale-remove-replica HOST=<host>"; exit 2; fi
	ansible-playbook playbooks/scale_remove_replica.yml -e target_host=$(HOST)
