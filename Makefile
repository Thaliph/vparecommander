.PHONY: all create-cluster start build deploy clean check install-deps test-resources

CLUSTER_NAME = vpa-recommender
IMAGE_NAME = vpa-recommender-operator:latest
KUBECTL = kubectl
KIND = kind

all: create-cluster build deploy test-resources

install-deps:
	@echo "Installing dependencies..."
	pip install kopf kubernetes pyyaml

create-cluster:
	@echo "Creating Kind cluster"
	$(KIND) create cluster --name $(CLUSTER_NAME) --wait 3m
	@echo "Installing VPA..."
	$(KUBECTL) apply -f https://raw.githubusercontent.com/kubernetes/autoscaler/vpa-release-1.0/vertical-pod-autoscaler/deploy/vpa-v1-crd-gen.yaml
	$(KUBECTL) apply -f https://raw.githubusercontent.com/kubernetes/autoscaler/vpa-release-1.0/vertical-pod-autoscaler/deploy/vpa-rbac.yaml
	$(KUBECTL) apply -f https://raw.githubusercontent.com/kubernetes/autoscaler/vpa-release-1.0/vertical-pod-autoscaler/deploy/recommender-deployment.yaml
	$(KUBECTL) apply -f https://raw.githubusercontent.com/kubernetes/autoscaler/vpa-release-1.0/vertical-pod-autoscaler/deploy/updater-deployment.yaml
	$(KUBECTL) apply -f https://raw.githubusercontent.com/kubernetes/autoscaler/vpa-release-1.0/vertical-pod-autoscaler/deploy/admission-controller-deployment.yaml
	@echo "Generating certificates for VPA Admission Controller..."
	curl -s https://raw.githubusercontent.com/kubernetes/autoscaler/master/vertical-pod-autoscaler/pkg/admission-controller/gencerts.sh | bash
	@echo "Waiting for VPA to be ready..."
	sleep 30

build:
	@echo "Building operator Docker image"
	docker build -t $(IMAGE_NAME) .
	$(KIND) load docker-image $(IMAGE_NAME) --name $(CLUSTER_NAME)

deploy:
	@echo "Installing CRD"
	$(KUBECTL) apply -f crds/vparecommender.yaml
	@echo "Creating operator RBAC and deployment"
	$(KUBECTL) apply -f manifests/deployment.yaml
	$(KUBECTL) apply -f manifests/rbac.yaml
	@echo "Waiting for operator to be ready..."
	sleep 10
	$(KUBECTL) -n vpa-recommender wait --for=condition=available --timeout=60s deployment/vpa-recommender-operator

test-resources:
	@echo "Deploying test resources..."
	$(KUBECTL) apply -f examples/sample-deployment.yaml
	$(KUBECTL) apply -f examples/sample-vpa.yaml
	@echo "Waiting for VPA to generate recommendations..."
	sleep 30
	@echo "Deploying VPARecommender..."
	$(KUBECTL) apply -f examples/example-recommender.yaml

check:
	@echo "Checking operator status"
	$(KUBECTL) -n vpa-recommender get pods
	$(KUBECTL) -n vpa-recommender get vparecommenders
	$(KUBECTL) -n vpa-recommender describe vparecommenders

clean:
	@echo "Cleaning up"
	$(KIND) delete cluster --name $(CLUSTER_NAME)

start: install-deps
	@echo "Running operator locally"
	cd operator && kopf run --verbose --standalone --all-namespaces operator.py
