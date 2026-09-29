# Pardis Shop

![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![nginx](https://img.shields.io/badge/nginx-1.27-009639?logo=nginx&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8-4479A1?logo=mysql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-5.0-DC382D?logo=redis&logoColor=white)
![Kubernetes](https://img.shields.io/badge/Kubernetes-CCE-326CE5?logo=kubernetes&logoColor=white)

A small, production-style **microservices e-commerce application** built for the
**Pardis Cloud Phase 1 – Single VPC Architecture**. Three containerized services run on
CCE (Kubernetes); data lives in the managed services the architecture guide prescribes:
RDS (MySQL), DCS (Redis) and OBS (object storage).

## Contents

- [Architecture](#architecture)
- [Features](#features)
- [Quick start (Docker Compose)](#quick-start-docker-compose)
- [Deploy to Pardis Cloud](#deploy-to-pardis-cloud)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Observability](#observability)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [Roadmap: Phase 2](#roadmap-phase-2)

## Architecture

```mermaid
flowchart LR
    U[Users] --> DNS[Cloud DNS] --> DDOS[Anti-DDoS] --> CFW[Cloud Firewall] --> WAF --> ELB
    subgraph VPC["VPC 10.0.0.0/16"]
        subgraph CCE["CCE cluster · subnet 10.0.2.0/24"]
            ING[Ingress] --> FE[frontend<br/>nginx]
            FE -->|/api| API[backend-api<br/>FastAPI]
            API -->|internal| ORD[order-service<br/>FastAPI]
        end
        subgraph DATA["Database subnet 10.0.3.0/24"]
            RDS[(RDS MySQL<br/>shop_db · order_db)]
            DCS[(DCS Redis 5)]
        end
    end
    ELB --> ING
    API --> RDS
    API --> DCS
    ORD --> RDS
    API --> OBS[(OBS<br/>ecommerce-images)]
    ORD --> OBS2[(OBS<br/>ecommerce-invoices)]
```

Traffic follows the guide exactly: `Ingress → Frontend → Backend API → Order Service`.
The order service is a `ClusterIP` service with no Ingress route; only `backend-api` can
call it (NetworkPolicy + shared internal token).

| Guide component | Implementation | Responsibility |
|---|---|---|
| Frontend (Pod/Service) | [`frontend/`](frontend) | Static web UI; reverse proxy `/api` → backend-api |
| Backend API (Pod/Service) | [`backend-api/`](backend-api) | Accounts, JWT auth, catalog, cart, checkout, admin, product images |
| Order Service (Pod/Service) | [`order-service/`](order-service) | Orders, cancellation, PDF invoices |
| Ingress | [`k8s/30-ingress.yaml`](k8s/30-ingress.yaml) | Host rule → frontend, TLS |
| RDS MySQL | `shop_db`, `order_db` | One database and one user per service |
| DCS Redis | backend-api | Cart, catalog cache, login rate limit, checkout lock |
| OBS | 3 private buckets | `ecommerce-images`, `ecommerce-invoices`, `ecommerce-backups` |
| Cloud Eye / LTS | stdout + `/metrics` | JSON logs with request IDs, Prometheus metrics |

## Features

**Customers** register and sign in, browse products with images, manage a cart, check
out with a shipping address, see their order history, cancel orders (stock is returned)
and download PDF invoices.

**Admins** create products, change stock, upload product images to OBS and see all orders.
The first admin account is created automatically from the Secret.

**Reliability**

- Checkout is idempotent: the same `Idempotency-Key` always returns the same order.
- A Redis lock blocks double-submits; stock is reserved with row locks in MySQL.
- If the order service fails, reserved stock is given back (compensation).
- Invoices are regenerated on demand if the OBS upload failed at checkout time.
- Catalog reads fall back to MySQL when Redis is unavailable.

**Security**

- All containers run as non-root (numeric UID) with a read-only root filesystem.
- Least-privilege Secrets: each Deployment only receives the keys it needs.
- Default-deny NetworkPolicy; passwords hashed with bcrypt; JWT access tokens.
- Security headers and a strict Content-Security-Policy on the frontend.

## Quick start (Docker Compose)

Requirements: Docker Engine with Compose v2.

```bash
git clone <this-repo> pardis-shop && cd pardis-shop
cp .env.example .env
docker compose up -d --build
docker compose ps                       # everything "healthy", migrate jobs "Exited (0)"
```

Open <http://localhost:8080> and sign in as `admin@example.com` / `admin12345`.

Run the end-to-end check:

```bash
BASE_URL=http://localhost:8080 ./scripts/smoke-test.sh
```

Local stand-ins for the managed services:

| Container | Stands in for | Notes |
|---|---|---|
| `mysql` (`mysql:8.4`) | RDS MySQL | Data kept in the `mysql-data` volume |
| `redis` (`redis:5.0-alpine`) | DCS Redis 5.0 | Password protected |
| `local-s3` (built from `python:3.12-slim`) | OBS | S3-compatible, **in memory**; creates the 3 buckets on start |
| `backend-migrate`, `order-migrate` | Kubernetes Jobs | Run once and exit with code `0` |

Reset everything with `docker compose down -v`.

## Deploy to Pardis Cloud

### 1. Prepare the cloud resources

Follow the Phase 1 network plan (VPC `10.0.0.0/16`; public, CCE, database and storage subnets).

1. **RDS MySQL 8** in the database subnet. From the Bastion Host, after changing the two
   passwords in the file: `mysql -h <rds-ip> -u root -p < scripts/rds-init.sql`.
   RDS-SG allows `3306` only from CCE-SG.
2. **DCS Redis 5.0 or later** in the database subnet. DCS-SG allows `6379` only from CCE-SG.
3. **OBS**: create `ecommerce-images`, `ecommerce-invoices` and `ecommerce-backups` (private).
   Enable versioning on invoices and backups and add lifecycle rules. Create an IAM user
   whose access keys are limited to these buckets. The OBS endpoint must be reachable from
   the CCE nodes (VPC endpoint or NAT Gateway).
4. **SWR**: create an organization for the images.
5. **CCE**: at least 3 nodes, an **ingress controller add-on bound to the ELB**, and
   **metrics-server** for HPA. Note the class name: `kubectl get ingressclass`.
6. **Cloud DNS**: record for your domain → ELB EIP. Get a TLS certificate for the domain.

### 2. Build and push the images

```bash
cp deploy.env.example deploy.env      # registry, tag, domain, ingress class, OBS endpoint
docker login ...                      # command from SWR console → "Generate Login Command"
./scripts/build-push.sh
```

### 3. Create the Secrets

```bash
cp k8s/02-secret.example.yaml k8s/02-secret.yaml     # git-ignored; fill in real values
kubectl apply -f k8s/00-namespace.yaml -f k8s/02-secret.yaml
kubectl -n pardis-shop create secret tls shop-tls --cert=fullchain.pem --key=privkey.pem
```

### 4. Deploy

```bash
./scripts/deploy.sh                   # schema jobs → services → rollout → dependency check
BASE_URL=https://shop.example.com ./scripts/smoke-test.sh
```

`deploy.sh` re-creates the migration Jobs on every run (Jobs are immutable), waits for
them, applies Deployments, Services, Ingress, HPA (CPU 70 % / memory 80 %, 2–6 pods),
PodDisruptionBudgets and NetworkPolicies, waits for the rollouts and prints each
service's dependency report. `./scripts/deploy.sh --render` only prints the final YAML.

## Configuration

### ConfigMap `shop-config`

| Key | Default | Notes |
|---|---|---|
| `OBS_ENDPOINT` | from `deploy.env` | `https://obs.<region>...` |
| `OBS_REGION` | from `deploy.env` | |
| `OBS_ADDRESSING_STYLE` | `virtual` | `path` for local-s3, or if bucket DNS names do not resolve |
| `OBS_SSE` | empty | Set `AES256` only if OBS accepts the SSE header |
| `OBS_BUCKET_IMAGES` | `ecommerce-images` | |
| `OBS_BUCKET_INVOICES` | `ecommerce-invoices` | Keys: `invoices/{year}/{month}/{number}.pdf` |
| `CURRENCY` | `USD` | |
| `ENVIRONMENT` | `production` | `development` enables Swagger at `/api/docs` |
| `LOG_LEVEL` | `INFO` | |

### Secret `shop-secrets`

| Key | Used by | Example |
|---|---|---|
| `BACKEND_DATABASE_URL` | backend-api | `mysql+pymysql://shop:***@10.0.3.10:3306/shop_db` |
| `ORDER_DATABASE_URL` | order-service | `mysql+pymysql://orders:***@10.0.3.10:3306/order_db` |
| `REDIS_URL` | backend-api | `redis://:***@10.0.3.20:6379/0` |
| `JWT_SECRET` | backend-api | `openssl rand -hex 32` |
| `INTERNAL_TOKEN` | both | `openssl rand -hex 32` (different value) |
| `OBS_ACCESS_KEY`, `OBS_SECRET_KEY` | both | IAM user limited to the buckets |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | backend-migrate Job | First admin account |

### `deploy.env`

`REGISTRY`, `TAG`, `DOMAIN`, `INGRESS_CLASS`, `PULL_SECRET`, `OBS_ENDPOINT`, `OBS_REGION`,
`SEED_DEMO_DATA`. See [`deploy.env.example`](deploy.env.example).

## API reference

All public endpoints are served under `/api/v1` through the frontend.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/register` | – | Create a customer account |
| `POST` | `/auth/login` | – | Get a JWT access token (rate limited) |
| `GET` | `/auth/me` | user | Current user |
| `GET` | `/products` | – | Active products (cached in Redis, `X-Cache` header) |
| `GET` | `/products/{id}` | – | One product |
| `GET` | `/products/{id}/image` | – | Product image streamed from OBS |
| `GET` `PUT` `DELETE` | `/cart` | user | Read, set an item quantity, clear |
| `DELETE` | `/cart/items/{product_id}` | user | Remove one item |
| `POST` | `/checkout` | user | Place an order; header `Idempotency-Key` required |
| `GET` | `/orders` | user | My orders |
| `GET` | `/orders/{id}` | user | One order |
| `POST` | `/orders/{id}/cancel` | user | Cancel and return stock |
| `GET` | `/orders/{id}/invoice` | user | Invoice PDF |
| `GET` `POST` | `/admin/products` | admin | List all / create product |
| `PATCH` | `/admin/products/{id}` | admin | Update name, price, stock, active flag |
| `POST` | `/admin/products/{id}/image` | admin | Upload JPEG/PNG/WebP (max 5 MiB) to OBS |
| `GET` | `/admin/orders` | admin | All orders |

Internal endpoints (not routed by the Ingress): `/healthz/live`, `/healthz/ready`,
`/healthz/deps`, `/metrics` on both services, and `/internal/orders/*` on order-service.

## Observability

- **Logs**: one JSON object per line on stdout (collect with LTS). Every request carries an
  `X-Request-ID`, forwarded from frontend to backend, so one request can be followed across
  services.
- **Metrics**: Prometheus format on `/metrics`: `http_requests_total`,
  `http_request_duration_seconds`, `shop_checkouts_total`, `shop_orders_total`,
  `shop_invoice_upload_failures_total`. Pods carry `prometheus.io/*` annotations.
- **Health**: `/healthz/live` (process up), `/healthz/ready` (RDS and DCS reachable),
  `/healthz/deps` (status of every dependency, for troubleshooting).

## Troubleshooting

Start with the dependency report:

```bash
# Kubernetes
kubectl -n pardis-shop exec deploy/backend-api   -- python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz/deps').read().decode())"
kubectl -n pardis-shop exec deploy/order-service -- python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz/deps').read().decode())"

# Docker Compose
docker compose exec backend-api   python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz/deps').read().decode())"
docker compose exec order-service python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz/deps').read().decode())"
```

Every value should be `ok`.

| Symptom | Likely cause | What to check |
|---|---|---|
| `ImagePullBackOff` | Wrong `REGISTRY`/`TAG` or pull secret | `kubectl -n pardis-shop describe pod <pod>`; `PULL_SECRET` in `deploy.env` |
| Migration Job fails | RDS unreachable, wrong password, database missing | `kubectl -n pardis-shop logs job/backend-migrate`; RDS-SG; `rds-init.sql` |
| Pod `Running` but `0/1 Ready` | RDS or DCS down or blocked | Pod logs (`readiness failed: ...`), `/healthz/deps` |
| `redis: unknown command HELLO` | Client speaking RESP3 to Redis 5 | Already fixed with `protocol=2`; keep it if you change the client |
| 404 on the domain | Ingress class or host mismatch | `kubectl get ingressclass`; `kubectl -n pardis-shop describe ingress` |
| 502/504 from the Ingress | Frontend pods not ready | `kubectl -n pardis-shop get pods,endpoints` |
| `/api/...` returns 502 | backend-api not ready | Frontend logs; backend `/healthz/deps` |
| Checkout 503 `order service unavailable` | order-service down or blocked | `kubectl -n pardis-shop get pods -l app=order-service` |
| Image upload / invoice 503 | OBS endpoint, keys, bucket, SSE, addressing style | `/healthz/deps`; try `OBS_ADDRESSING_STYLE=path` and `OBS_SSE=""` |
| `invalid internal token` | `INTERNAL_TOKEN` differs between pods | Restart both Deployments after changing the Secret |
| HPA shows `<unknown>` | metrics-server missing | `kubectl top pods -n pardis-shop` |
| Prometheus cannot scrape | `default-deny-ingress` NetworkPolicy | Add an allow rule for the monitoring namespace |
| Compose: images or invoices gone | local-s3 restarted (in-memory) | Upload again; OBS on the cloud is persistent |

Useful commands:

```bash
kubectl -n pardis-shop get pods -o wide
kubectl -n pardis-shop logs deploy/backend-api --tail=100 -f
kubectl -n pardis-shop rollout restart deploy/backend-api      # after changing ConfigMap/Secret
kubectl -n pardis-shop port-forward svc/frontend 8080:8080     # bypass ELB, Ingress and WAF
docker compose logs -f backend-api order-service
```

## Project structure

```
.
├── backend-api/            FastAPI: auth, catalog, cart, checkout, admin, images
│   ├── app/                main.py, models, schemas, security, storage (OBS), migrate.py
│   ├── Dockerfile          python:3.12-slim, UID 10001
│   └── requirements.txt
├── order-service/          FastAPI: orders, cancellation, PDF invoices (internal only)
├── frontend/               nginx:1.27-alpine (UID 101) + vanilla JS storefront
│   ├── nginx.conf          static files, /api proxy, security headers, JSON access log
│   └── public/
├── local-s3/               S3-compatible server for Docker Compose only (stands in for OBS)
├── k8s/                    CCE manifests (placeholders are filled by scripts/deploy.sh)
│   ├── 00-namespace.yaml   01-configmap.yaml   02-secret.example.yaml
│   ├── 10-migrate-jobs.yaml
│   ├── 20-backend-api.yaml 21-order-service.yaml 22-frontend.yaml
│   ├── 30-ingress.yaml     40-hpa.yaml 41-pdb.yaml 50-networkpolicy.yaml
├── scripts/
│   ├── build-push.sh       build the 3 images and push to SWR
│   ├── deploy.sh           render and apply k8s/ in order
│   ├── smoke-test.sh       end-to-end check through the public URL
│   ├── rds-init.sql        databases and users on RDS
│   └── mysql-init.sh       same, for the local MySQL container
├── docker-compose.yaml
├── deploy.env.example
└── .env.example
```

Files with real secrets (`.env`, `deploy.env`, `k8s/02-secret.yaml`) are git-ignored.

## Roadmap: Phase 2

- Split into an application VPC and a data/shared-services VPC (peering or Cloud Connect).
- RDS primary/standby across AZs with automatic failover; read replicas for catalog reads.
- KMS-managed encryption keys; cross-region replication for OBS.
- Alembic migrations instead of `create_all`.
