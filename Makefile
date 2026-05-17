# pigsty-lite operator entry points.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
FAIL_FAST ?= 1

include Makefile.d/lint.mk
include Makefile.d/images.mk

.PHONY: help init configure plan deploy switchover failover minor-upgrade scale-add-replica scale-remove-replica lint images test-image test-role clean

help:
	@echo "pigsty-lite - operator commands"
	@echo
	@echo "  Deploy actions:"
	@echo "  make init                          Set up control node (Galaxy collections + roles)"
	@echo "  make configure                     Interactive wizard; emits inventory + response file"
	@echo "  make plan                          Run site.yml in --check --diff mode"
	@echo "  make deploy                        Run site.yml against the active inventory"
	@echo
	@echo "  Operations actions:"
	@echo "  make switchover                     Controlled primary switchover"
	@echo "  make failover CANDIDATE=<host>      Manual failover to a named candidate"
	@echo "  make minor-upgrade                  Rolling minor PostgreSQL upgrade"
	@echo "  make scale-add-replica HOST=<host>  Add a replica (host must be in inventory)"
	@echo "  make scale-remove-replica HOST=<host>  Decommission a replica"
	@echo
	@echo "  Dev/testing actions:"
	@echo "  make lint                          Run all linters"
	@echo "  make images                        Build all three molecule base images (common/data/infra)"
	@echo "  make test-role ROLE=<name>         Run all Molecule scenarios for a single role"
	@echo "  make test-role ROLE=<name> FAIL_FAST=0  Keep running verify tasks after failures"
	@echo "  make clean                         Remove generated artifacts"

init:
	ansible-galaxy collection install -r requirements.yml -p ./collections --upgrade
	ansible-galaxy role install -r requirements.yml -p ./roles.galaxy

configure:
	./configure

plan: init
	ansible-playbook playbooks/site.yml --check --diff

deploy: init
	ansible-playbook playbooks/site.yml

test-role: images
	@if [ -z "$(ROLE)" ]; then echo "Usage: make test-role ROLE=<name> [FAIL_FAST=0]"; exit 2; fi
	@if [ "$(FAIL_FAST)" = "0" ]; then \
		cd tests/molecule/$(ROLE); \
		log_file=$$(mktemp); \
		trap 'rm -f "$$log_file"' EXIT; \
		status=0; \
		ANSIBLE_HOME=/tmp/pigsty-lite-ansible MOLECULE_TASK_IGNORE_ERRORS=1 MOLECULE_GLOB='molecule/*/molecule.yml' molecule test --all 2>&1 | tee "$$log_file" || status=$$?; \
		if grep -Eq 'ignored=[1-9][0-9]*' "$$log_file"; then status=1; fi; \
		exit $$status; \
	else \
		cd tests/molecule/$(ROLE) && ANSIBLE_HOME=/tmp/pigsty-lite-ansible MOLECULE_GLOB='molecule/*/molecule.yml' molecule test --all; \
	fi

clean:
	rm -rf inventory/site.yml group_vars/response.yml .ansible/
	find tests/molecule -path '*/_tmp*' -exec rm -rf {} +
	find . -name __pycache__ -type d -exec rm -rf {} +
	find . -name '*.pyc' -type f -delete
	@if command -v podman >/dev/null 2>&1; then \
		podman images --format "{{.Repository}}:{{.Tag}}" | \
			grep -E '^localhost/molecule-base(-common|-data|-infra)?:' | \
			xargs -r podman image rm -f; \
	fi

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
