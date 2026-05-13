# pigsty-lite operator entry points.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

include Makefile.d/lint.mk

.PHONY: help init configure plan deploy clean test-role

help:
	@echo "pigsty-lite - operator commands"
	@echo
	@echo "  make init          Set up the control node (install Galaxy collections + roles)"
	@echo "  make configure     Interactive wizard; emits inventory + response file"
	@echo "  make plan          Run site.yml in --check --diff mode"
	@echo "  make deploy        Run site.yml against the active inventory"
	@echo "  make lint          Run all linters"
	@echo "  make test-role ROLE=<name>   Run molecule for a single role"
	@echo "  make clean         Remove generated artifacts"

init:
	ansible-galaxy collection install -r requirements.yml -p ./collections
	ansible-galaxy role install -r requirements.yml -p ./roles.galaxy

configure:
	./configure

plan: init
	ansible-playbook playbooks/site.yml --check --diff

deploy: init
	ansible-playbook playbooks/site.yml

test-role:
	@if [ -z "$(ROLE)" ]; then echo "Usage: make test-role ROLE=<name>"; exit 2; fi
	cd tests/molecule/$(ROLE) && molecule test

clean:
	rm -rf .ansible/facts dist/ artifacts/
	find . -name __pycache__ -type d -exec rm -rf {} +
