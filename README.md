# Multi-Cloud Intelligent Traffic Router

Production-grade disaster-resistant traffic routing between AWS and GCP with real health probes, dynamic scoring, automated failover, Prometheus metrics, Grafana dashboards, Kubernetes HPA, and GitHub Actions CI.

## Architecture

```text
			+-----------------------------+
			| Public DNS / Global Edge    |
Client Request -------->| Nginx Service (global-edge) |
			+-------------+---------------+
				      |
				      v
			+-----------------------------+
			| Intelligent Traffic Router  |
			| FastAPI + decision engine   |
			+------+------+---------------+
			       |      |
		  probe/score  |      |  proxy requests
			       |      |
		 +-------------+      +--------------+
		 v                                   v
      +-------------------------+        +-------------------------+
      | AWS app cluster         |        | GCP app cluster         |
      | sample-app on EKS       |        | sample-app on GKE       |
      | HPA + /health + metrics |        | HPA + /health + metrics |
      +------------+------------+        +------------+------------+
		   \                              /
		    \                            /
		     v                          v
		+-------------------------------------+
		| Prometheus + Prometheus Adapter     |
		| scrape health, latency, error rate  |
		+----------------+--------------------+
				 |
				 v
			 +---------------+
			 | Grafana       |
			 | dashboards    |
			 +---------------+
```

## Reliability model

The router never switches randomly. Each backend is scored from four real signals:

1. Probe health from `/health` every 2 seconds.
2. Request latency observed by the router.
3. Rolling error rate over the last 60 seconds.
4. Current inflight load.

The hard failover rule is implemented in code in [services/router/app/engine.py](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/services/router/app/engine.py):

1. If health checks fail, the backend is removed from routing.
2. If latency exceeds the threshold, the backend is removed from routing.
3. If error rate exceeds 5%, the backend is removed from routing.
4. If inflight load exceeds the limit, the backend is removed from routing.
5. If multiple backends are healthy, weighted dynamic routing is used based on decision score.

## Repository structure

```text
.
├── .github/workflows/ci-cd.yaml
├── deploy/
│   └── kubernetes/
│       ├── base/
│       └── overlays/
│           ├── aws/
│           ├── gcp/
│           └── local-sim/
├── docs/
│   ├── deployment-guide.md
│   └── failure-simulation.md
├── services/
│   ├── router/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/
│   └── sample-app/
│       ├── Dockerfile
│       ├── requirements.txt
│       └── app/
└── tests/test_engine.py
```

## What is implemented

1. Multi-cloud application deployment manifests for AWS and GCP.
2. Local single-cluster simulation to prove failover logic.
3. FastAPI application with `/health`, `/ready`, `/metrics`, latency metrics, and CPU-driving workload endpoints.
4. FastAPI intelligent traffic router with active probes, dynamic scoring, automatic failover, and request proxying.
5. Prometheus scrape configuration and Prometheus Adapter custom metrics for HPA traffic scaling.
6. Grafana dashboard for active cloud, traffic distribution, failovers, latency, and error rate.
7. GitHub Actions pipeline for test and container build validation.

## Core files

1. Router service: [services/router/app/main.py](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/services/router/app/main.py)
2. Decision engine: [services/router/app/engine.py](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/services/router/app/engine.py)
3. Demo application: [services/sample-app/app/main.py](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/services/sample-app/app/main.py)
4. Base Kubernetes stack: [deploy/kubernetes/base/kustomization.yaml](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/deploy/kubernetes/base/kustomization.yaml)
5. AWS deployment overlay: [deploy/kubernetes/overlays/aws/kustomization.yaml](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/deploy/kubernetes/overlays/aws/kustomization.yaml)
6. GCP deployment overlay: [deploy/kubernetes/overlays/gcp/kustomization.yaml](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/deploy/kubernetes/overlays/gcp/kustomization.yaml)
7. Deployment runbook: [docs/deployment-guide.md](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/docs/deployment-guide.md)
8. Failure demo runbook: [docs/failure-simulation.md](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/docs/failure-simulation.md)

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r services/router/requirements.txt
pip install -r services/sample-app/requirements.txt
pip install pytest
pytest -q
```

Build the images:

```bash
docker build -t multi-cloud-router/router:latest services/router
docker build -t multi-cloud-router/sample-app:latest services/sample-app
```

Run the full stack locally with Docker Compose:

```bash
docker compose -f deploy/docker-compose/docker-compose.yml up -d --build
curl http://localhost:8080/api/echo
curl http://localhost:8080/router/status
```

Router console (responsive UI):

1. Open `http://localhost:8080/`.
2. Use live controls to set/clear manual backend override.
3. Choose `preferred` mode to keep SRE safety rails or `strict` mode to force a backend selection for incident drills.
3. Watch active backend, backend health, and failover events update in real time.

Deploy the local simulation:

```bash
kind create cluster --name traffic-router
kubectl apply -k deploy/kubernetes/overlays/local-sim
kubectl port-forward svc/global-edge -n control-plane 8080:80
kubectl port-forward svc/grafana -n control-plane 3000:3000
```

Send traffic:

```bash
curl http://localhost:8080/
curl http://localhost:8080/router/status
```

## Demo sequence

1. Start continuous traffic through `global-edge`.
2. Scale `sample-app-aws` to zero.
3. Watch `router_failover_events_total` increment.
4. Confirm `router_active_backend` flips to `gcp-secondary`.
5. Restore AWS and observe score-based re-entry.

Exact commands are in [docs/failure-simulation.md](c:/Users/shiva/Downloads/Multi-Cloud-Intelligent-Traffic-Router/docs/failure-simulation.md).
