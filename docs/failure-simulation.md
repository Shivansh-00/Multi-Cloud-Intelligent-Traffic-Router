# Failure Simulation Guide

## Local simulation

Deploy the full stack:

```bash
kind create cluster --name traffic-router
kubectl apply -k deploy/kubernetes/overlays/local-sim
kubectl port-forward svc/global-edge -n control-plane 8080:80
kubectl port-forward svc/grafana -n control-plane 3000:3000
```

## Docker Compose simulation

If you want a full live demo without pushing images to a registry first:

```powershell
./scripts/run-local-compose-demo.ps1
```

That script:

1. Builds and starts the full stack.
2. Sends warm-up traffic through `global-edge`.
3. Stops the AWS backend.
4. Waits for the router probe loop to mark AWS unhealthy.
5. Sends more traffic and prints the failover state as JSON.

Manual compose flow:

```bash
docker compose -f deploy/docker-compose/docker-compose.yml up -d --build
curl http://localhost:8080/router/status
docker compose -f deploy/docker-compose/docker-compose.yml stop sample-app-aws
curl http://localhost:8080/router/status
docker compose -f deploy/docker-compose/docker-compose.yml up -d sample-app-aws
```

Tear the compose stack down when finished:

```powershell
./scripts/stop-local-compose-demo.ps1
```

Generate traffic:

```bash
for /L %i in (1,1,50) do curl http://localhost:8080/api/echo
```

Open the control console:

1. `http://localhost:8080/`
2. Observe active backend and backend health.
3. Optionally test manual override controls.
4. Use strict override only for controlled drills, because strict mode can intentionally route to a degraded backend.

Inspect the router state:

```bash
kubectl port-forward svc/intelligent-router -n control-plane 18080:8080
curl http://localhost:18080/router/status
```

Simulate an AWS outage:

```bash
kubectl scale deployment sample-app-aws --replicas=0 -n aws-sim
```

Expected behavior within roughly 4 seconds:

1. Router probes fail twice.
2. `router_backend_health{backend="aws-primary"}` drops to `0`.
3. `router_failover_events_total{from_backend="aws-primary",to_backend="gcp-secondary"}` increments.
4. All subsequent traffic is served by GCP.

Restore AWS:

```bash
kubectl scale deployment sample-app-aws --replicas=2 -n aws-sim
```

## Scaling demonstration

Generate traffic and CPU load:

```bash
for /L %i in (1,1,200) do curl "http://localhost:8080/api/process?work_units=200000"
```

Watch HPA decisions:

```bash
kubectl get hpa -A -w
```
