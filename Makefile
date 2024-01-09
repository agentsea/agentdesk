
.PHONY: publish
publish:
	rm -rf dist
	poetry build
	poetry publish

.PHONY: clear-poetry-cache
clear_poetry_cache:
	poetry cache clear --all pypi

.PHONY: integration
integration:
	poetry run python ./tests/integration.py