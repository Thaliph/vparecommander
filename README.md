# VPA Recommender Operator

This is a Kubernetes operator built with [Kopf](https://github.com/nolar/kopf) that automates the process of taking Vertical Pod Autoscaler (VPA) recommendations and applying them to your workloads through Git-based pull requests.

## Features

- Retrieves resource recommendations from VPA resources
- Automatically creates patches for CPU and memory requests/limits
- Creates pull requests to your Git repository with the recommended changes
- Uses Kustomize-compatible patch format
- Can be scheduled to run periodically
- Stores recommendation history in the CRD status

## Prerequisites

- Kubernetes cluster with VPA installed
- GitHub repository for storing Kustomize patches
- GitHub personal access token with repo permissions
- Python 3.9+ (for local development)

## Installation

### 1. Create a GitHub personal access token

Create a token with `repo` permissions.

### 2. Create a Secret with your GitHub token

```bash
kubectl create namespace vpa-recommender
kubectl create secret generic git-credentials -n vpa-recommender --from-literal=token=your-github-token
```

### 3. Install the CRD and operator

```bash
kubectl apply -f crds/vparecommender.yaml
kubectl apply -f manifests/rbac.yaml
kubectl apply -f manifests/deployment.yaml
```

## Usage

1. Create a VPA for your workload:

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: my-app-vpa
  namespace: default
spec:
  targetRef:
    apiVersion: "apps/v1"
    kind: Deployment
    name: my-app
  updatePolicy:
    updateMode: "Off"  # Only recommend, don't apply changes automatically
```

2. Create a VPARecommender resource:

```yaml
apiVersion: recommander.k8s.io/v1
kind: VPARecommender
metadata:
  name: my-app-recommender
  namespace: vpa-recommender
spec:
  vpaName: my-app-vpa
  vpaNamespace: default
  gitRepo: "https://github.com/yourorg/your-repo.git"
  gitPath: "kustomize-configs"
  targetResource:
    kind: Deployment
    name: my-app
    namespace: default
    containerIndex: 0
  secretRef: git-credentials
```

3. The operator will create a pull request with patches like:

```yaml
- op: add
  path: "/spec/template/spec/containers/0/resources/requests/cpu" 
  value: 300m
- op: add
  path: "/spec/template/spec/containers/0/resources/limits/cpu" 
  value: 600m
```

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the operator locally
kopf run operator/operator.py --verbose
```

## Testing with Kind

The included Makefile provides commands for testing with Kind:

```bash
# Create a cluster and deploy the operator
make all

# Run the operator locally
make start

# Clean up
make clean
```

## License

MIT
