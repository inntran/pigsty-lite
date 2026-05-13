# Lint targets - included by top-level Makefile.

.PHONY: lint lint-yaml lint-ansible lint-python lint-markdown lint-shell lint-xml

lint: lint-yaml lint-ansible lint-python lint-markdown lint-shell lint-xml

lint-yaml:
	yamllint .

lint-ansible:
	ansible-lint

lint-python:
	ruff check .
	ruff format --check .

lint-markdown:
	markdownlint-cli2 "**/*.md" "#collections" "#.ansible" "#docs/superpowers"

lint-shell:
	@files=$$(find bin -type f -not -name '*.py' -not -name '_*.py' 2>/dev/null); \
	if [ -n "$$files" ]; then shellcheck $$files; fi

lint-xml:
	@if compgen -G "files/firewalld/services/*.xml" > /dev/null; then \
		xmllint --noout files/firewalld/services/*.xml; \
	fi
