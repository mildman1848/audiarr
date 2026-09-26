.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
IMAGE_NAME ?= audiarr
APP_VERSION ?= 0.6.1
IMAGE_REVISION ?= release
VERSION ?= $(APP_VERSION)
IMAGE_TAG ?= $(VERSION)
REGISTRY ?= ghcr.io/mildman1848
IMAGE_REF ?= $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
LOCAL_IMAGE ?= local/$(IMAGE_NAME):$(IMAGE_TAG)
DOCKER ?= docker
COMPOSE ?= $(DOCKER) compose
LOAD_PLATFORM ?= linux/amd64
PLATFORMS ?= linux/amd64,linux/arm64

.PHONY: help info lint test validate compose-config build smoke security release-dry-run clean

help: ## Show available targets.
	@python3 scripts/make_help.py $(MAKEFILE_LIST)

info: ## Print image/build metadata.
	@printf 'IMAGE_NAME=%s\n' '$(IMAGE_NAME)'
	@printf 'APP_VERSION=%s\n' '$(APP_VERSION)'
	@printf 'IMAGE_REVISION=%s\n' '$(IMAGE_REVISION)'
	@printf 'VERSION=%s\n' '$(VERSION)'
	@printf 'IMAGE_REF=%s\n' '$(IMAGE_REF)'
	@printf 'LOCAL_IMAGE=%s\n' '$(LOCAL_IMAGE)'

lint: ## Run static checks.
	@PYTHON='$(PYTHON)' scripts/lint-static.sh

test: ## Run pytest.
	@$(PYTHON) -m pytest

validate: lint test ## Run all non-Docker validation.
	@[[ '$(VERSION)' == '$(APP_VERSION)' ]] || { echo 'ERROR: VERSION mismatch' >&2; exit 2; }
	@if command -v actionlint >/dev/null 2>&1; then actionlint; else echo 'WARN: actionlint missing; skipped'; fi
	@echo 'OK: validation passed'

compose-config: ## Validate Compose file.
	@$(COMPOSE) config >/tmp/audiarr-compose.yml
	@echo 'OK: compose config valid'

build: ## Build local single-platform Docker image.
	@DOCKER='$(DOCKER)' IMAGE_NAME='local/$(IMAGE_NAME)' IMAGE_TAG='$(IMAGE_TAG)' VERSION='$(VERSION)' APP_VERSION='$(APP_VERSION)' IMAGE_REVISION='$(IMAGE_REVISION)' PLATFORMS='$(LOAD_PLATFORM)' scripts/buildx-build.sh --load

smoke: ## Smoke-test local Docker image, if Docker daemon is available.
	@DOCKER='$(DOCKER)' IMAGE='$(LOCAL_IMAGE)' scripts/smoke.sh

security: ## Run local Trivy HIGH/CRITICAL config scan (same gate as CI; fails on unignored findings).
	@if command -v trivy >/dev/null 2>&1; then trivy config --severity HIGH,CRITICAL --exit-code 1 .; else echo 'ERROR: trivy not installed; see docs/release-hardening.md' >&2; exit 2; fi

release-dry-run: ## Show intended publish targets.
	@printf 'Would publish: %s\n' '$(IMAGE_REF)'
	@printf 'Tags: %s, latest, sha-%s\n' '$(IMAGE_TAG)' "$$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
	@printf 'Platforms: %s\n' '$(PLATFORMS)'

clean: ## Remove generated local artifacts.
	@scripts/clean.py
