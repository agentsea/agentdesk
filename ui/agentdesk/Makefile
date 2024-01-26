IMAGE_REPO := us-central1-docker.pkg.dev/agentsea-dev/agentdesk/ui
TAG := latest


.PHONY: serve
serve:
	npm start

.PHONY: build-img
build-img:
	docker buildx build --platform linux/amd64,linux/arm64 --push -t ${IMAGE_REPO}:${TAG}  .

.PHONY: push-img
push-img:
	docker push ${IMAGE_REPO}:${TAG}

# .PHONY: deploy
# deploy:
# 	helm install -n hub -f ./deploy/helm/agentsea-hub-ui/values.yaml hubapi ./deploy/helm/agentsea-hub-api

# .PHONY: package-chart
# package-chart:
# 	rm -rf ./pkg
# 	mkdir -p ./pkg
# 	helm package ./deploy/helm/agentsea-hub-ui -d ./pkg

# .PHONY: push-chart
# push-chart: package-chart
# 	$(eval CHART_FILE=$(shell ls ./pkg | head -n1))
# 	helm push ./pkg/$(CHART_FILE) oci://us-central1-docker.pkg.dev/agentsea-dev/hub
# 	rm -rf ./pkg

# .PHONY: release
# release: build-img push-chart