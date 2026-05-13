# pigsty-lite operator entry points.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

include Makefile.d/lint.mk

.PHONY: help configure plan deploy clean test-role install-collections

help:
	@echo "pigsty-lite - operator commands"
	@echo
	@echo "  make configure     Interactive wizard; emits inventory + response file"
	@echo "  make plan          Run site.yml in --check --diff mode"
	@echo "  make deploy        Run site.yml against the active inventory"
	@echo "  make lint          Run all linters"
	@echo "  make test-role ROLE=<name>   Run molecule for a single role"
	@echo "  make install-collections     Install Galaxy collections"
	@echo "  make clean         Remove generated artifacts"

install-collections:
	ansible-galaxy collection install -r requirements.yml -p ./collections

configure:
	./configure

plan: install-collections
	ansible-playbook playbooks/site.yml --check --diff

deploy: install-collections
	ansible-playbook playbooks/site.yml

test-role:
	@if [ -z "$(ROLE)" ]; then echo "Usage: make test-role ROLE=<name>"; exit 2; fi
	cd tests/molecule/$(ROLE) && molecule test

clean:
	rm -rf .ansible/facts dist/ artifacts/
	find . -name __pycache__ -type d -exec rm -rf {} +
