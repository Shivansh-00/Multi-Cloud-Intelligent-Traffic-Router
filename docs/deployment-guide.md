# Deployment Guide

## Production topology

1. Deploy the sample application to EKS using [deploy/kubernetes/overlays/aws/kustomization.yaml](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/deploy/kubernetes/overlays/aws/kustomization.yaml).
2. Deploy the same application to GKE using [deploy/kubernetes/overlays/gcp/kustomization.yaml](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/deploy/kubernetes/overlays/gcp/kustomization.yaml).
3. Deploy the control plane from [deploy/kubernetes/base/kustomization.yaml](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/deploy/kubernetes/base/kustomization.yaml) to a management cluster.
4. Update the router ConfigMap in [deploy/kubernetes/base/router.yaml](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/deploy/kubernetes/base/router.yaml) so `BACKENDS_JSON` points to the public load balancer addresses of the EKS and GKE services.
5. Point public DNS at the `global-edge` Service.

## Build and publish images

```bash
docker build -t multi-cloud-router/sample-app:latest services/sample-app
docker build -t multi-cloud-router/router:latest services/router
```

For AWS ECR:

```bash
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=us-east-1
ECR_BASE="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

aws ecr create-repository --repository-name multi-cloud-router/sample-app
aws ecr create-repository --repository-name multi-cloud-router/router
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_BASE
docker tag multi-cloud-router/sample-app:latest $ECR_BASE/multi-cloud-router/sample-app:latest
docker tag multi-cloud-router/router:latest $ECR_BASE/multi-cloud-router/router:latest
docker push $ECR_BASE/multi-cloud-router/sample-app:latest
docker push $ECR_BASE/multi-cloud-router/router:latest
```

For Google Artifact Registry:

```bash
GCP_PROJECT_ID=$(gcloud config get-value project)
GAR_REGION=us-central1
GAR_BASE="$GAR_REGION-docker.pkg.dev/$GCP_PROJECT_ID/multi-cloud-router"

gcloud auth configure-docker us-central1-docker.pkg.dev
gcloud artifacts repositories create multi-cloud-router --repository-format=docker --location=us-central1
docker tag multi-cloud-router/sample-app:latest $GAR_BASE/sample-app:latest
docker tag multi-cloud-router/router:latest $GAR_BASE/router:latest
docker push $GAR_BASE/sample-app:latest
docker push $GAR_BASE/router:latest
```

## Create clusters

EKS:

```bash
ECR_SAMPLE_APP_IMAGE="$ECR_BASE/multi-cloud-router/sample-app:latest"

eksctl create cluster --name traffic-router-eks --region us-east-1 --nodes 3 --managed
aws eks update-kubeconfig --name traffic-router-eks --region us-east-1
kubectl apply -k deploy/kubernetes/overlays/aws
kubectl set image deployment/sample-app sample-app=$ECR_SAMPLE_APP_IMAGE -n traffic-app
kubectl get svc sample-app -n traffic-app
```

GKE:

```bash
GAR_SAMPLE_APP_IMAGE="$GAR_BASE/sample-app:latest"

gcloud container clusters create traffic-router-gke --region us-central1 --num-nodes 3
gcloud container clusters get-credentials traffic-router-gke --region us-central1
kubectl apply -k deploy/kubernetes/overlays/gcp
kubectl set image deployment/sample-app sample-app=$GAR_SAMPLE_APP_IMAGE -n traffic-app
kubectl get svc sample-app -n traffic-app
```

Management cluster:

```bash
ROUTER_IMAGE=$ECR_BASE/multi-cloud-router/router:latest

kind create cluster --name traffic-router-control
kubectl apply -k deploy/kubernetes/base
kubectl set image deployment/intelligent-router router=$ROUTER_IMAGE -n control-plane
kubectl set image deployment/sample-app-aws sample-app=$ECR_SAMPLE_APP_IMAGE -n aws-sim
kubectl set image deployment/sample-app-gcp sample-app=$GAR_SAMPLE_APP_IMAGE -n gcp-sim
```

The base manifest deploys a fully local simulation. In production, replace `BACKENDS_JSON` in the router ConfigMap with the EKS and GKE public service URLs.

## Access endpoints

```bash
kubectl port-forward svc/global-edge -n control-plane 8080:80
kubectl port-forward svc/grafana -n control-plane 3000:3000
kubectl port-forward svc/prometheus -n control-plane 9090:9090
```

Then open:

1. `http://localhost:8080/`
2. `http://localhost:3000/` (anonymous access enabled; no login required)
3. `http://localhost:9090/`
