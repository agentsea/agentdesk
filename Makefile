
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