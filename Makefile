# pigsty-lite operator entry points.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
FAIL_FAST ?= 1
PROFILE ?= 0

ifeq ($(PROFILE),1)
MOLECULE_PROFILE_ENV := ANSIBLE_CALLBACKS_ENABLED=profile_tasks,profile_roles,timer
else
MOLECULE_PROFILE_ENV :=
endif

include Makefile.d/lint.mk
include Makefile.d/images.mk

.PHONY: help init configure regen plan deploy switchover failover minor-upgrade scale-add-replica scale-remove-replica lint images test-image test test-configure clean

RESPONSE_FILE ?= responses/site.rsp.yml

PYTEST ?= .venv/bin/pytest

help:
	@echo "pigsty-lite - operator commands"
	@echo
	@echo "  Deploy actions:"
	@echo "  make init                          Set up control node (Galaxy collections + roles)"
	@echo "  make configure                     Interactive wizard; emits inventory + response file"
	@echo "  make regen                         Regenerate inventory/site.yml from responses/site.rsp.yml"
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
	@echo "  make test                          Run configure unit tests (pytest)"
	@echo "  make test ROLE=<name>              Run all Molecule scenarios for a single role"
	@echo "  make test ROLE=all                 Run configure tests, then all Molecule roles"
	@echo "  make test ROLE=<name> FAIL_FAST=0  Keep running verify tasks after failures"
	@echo "  make test ROLE=<name> PROFILE=1    Enable profile_tasks/profile_roles/timer callbacks"
	@echo "  make test-configure                Run configure unit tests (pytest)"
	@echo "  make clean                         Remove generated artifacts"

init:
	ansible-galaxy collection install -r requirements.yml --upgrade
	ansible-galaxy role install -r requirements.yml -p ./roles.galaxy

configure:
	./configure

# Regenerate the deployable inventory from the response file (source of truth).
# plan/deploy depend on this so an edited responses/site.rsp.yml is never stale.
regen: $(RESPONSE_FILE)
	@if [ ! -f "$(RESPONSE_FILE)" ]; then \
		echo "ERROR: $(RESPONSE_FILE) not found; run 'make configure' first"; exit 1; \
	fi
	./configure -s -f $(RESPONSE_FILE) --no-vault

plan: init regen
	ansible-playbook playbooks/site.yml --check --diff

deploy: init regen
	ansible-playbook playbooks/site.yml

test-configure:
	$(PYTEST) tests/configure -v

test:
	@if [ -z "$(ROLE)" ]; then \
		$(MAKE) test-configure; \
	elif [ "$(ROLE)" = "all" ]; then \
		$(MAKE) test-configure || exit $$?; \
		$(MAKE) images || exit $$?; \
		status=0; \
		for role in $$(find tests/molecule -mindepth 4 -maxdepth 4 -name molecule.yml -printf '%h\n' | awk -F/ '{print $$3}' | sort -u); do \
			echo "==> molecule role: $$role"; \
			$(MAKE) test ROLE=$$role FAIL_FAST=$(FAIL_FAST) || status=$$?; \
		done; \
		exit $$status; \
	else \
		$(MAKE) images || exit $$?; \
		if [ "$(FAIL_FAST)" = "0" ]; then \
			cd tests/molecule/$(ROLE); \
			log_file=$$(mktemp); \
			trap 'rm -f "$$log_file"' EXIT; \
			status=0; \
			$(MOLECULE_PROFILE_ENV) ANSIBLE_LOCAL_TMP=/tmp/pigsty-lite-ansible/tmp MOLECULE_TASK_IGNORE_ERRORS=1 MOLECULE_GLOB='molecule/*/molecule.yml' molecule test --all 2>&1 | tee "$$log_file" || status=$$?; \
			if grep -Eq 'ignored=[1-9][0-9]*' "$$log_file"; then status=1; fi; \
			exit $$status; \
		else \
			cd tests/molecule/$(ROLE) && $(MOLECULE_PROFILE_ENV) ANSIBLE_LOCAL_TMP=/tmp/pigsty-lite-ansible/tmp MOLECULE_GLOB='molecule/*/molecule.yml' molecule test --all; \
		fi; \
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
