
.PHONY: publish
publish:
	rm -rf dist
	poetry build
	poetry publish

.PHONY: clear-poetry-cache
clear_poetry_cache:
	poetry cache clear --all pypi

.PHONY: start-ui 
start-ui:
	cd ui/agentdesk && npm start &

.PHONY: integration
integration: start-ui
	poetry run python ./tests/integration.py

.PHONY: test-gce
test-gce:
	poetry run python ./tests/desktop/gce.py

.PHONY: test-qemu
test-qemu:
	poetry run python ./tests/desktop/qemu.py