#!/usr/bin/env bash
# Render k8s/ with values from deploy.env and apply them in the right order.
#   ./scripts/deploy.sh            -> deploy
#   ./scripts/deploy.sh --render   -> only print the rendered YAML
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f deploy.env ] || { echo "deploy.env not found (cp deploy.env.example deploy.env)" >&2; exit 1; }
# shellcheck disable=SC1091
source deploy.env
NS=pardis-shop

render() {
  sed -e "s#__REGISTRY__#${REGISTRY}#g" \
      -e "s#__TAG__#${TAG}#g" \
      -e "s#__DOMAIN__#${DOMAIN}#g" \
      -e "s#__INGRESS_CLASS__#${INGRESS_CLASS}#g" \
      -e "s#__ELB_ID__#${ELB_ID}#g" \
      -e "s#__ELB_CLASS__#${ELB_CLASS:-performance}#g" \
      -e "s#__PULL_SECRET__#${PULL_SECRET}#g" \
      -e "s#__OBS_ENDPOINT__#${OBS_ENDPOINT}#g" \
      -e "s#__OBS_REGION__#${OBS_REGION}#g" \
      -e "s#__OBS_BUCKET_IMAGES__#${OBS_BUCKET_IMAGES:-ecommerce-images}#g" \
      -e "s#__OBS_BUCKET_INVOICES__#${OBS_BUCKET_INVOICES:-ecommerce-invoices}#g" \
      -e "s#__SEED_DEMO_DATA__#${SEED_DEMO_DATA}#g" \
      -e "s#__POD_SG_ID__#${POD_SG_ID:-}#g" \
      -e "s#__TLS_CERT_ID__#${TLS_CERT_ID:-}#g" \
      "$@"
}

if [ "${1:-}" = "--render" ]; then
  for f in k8s/[0-9]*.yaml; do case "$f" in k8s/02-*) continue ;; esac; echo "---"; render "$f"; done
  exit 0
fi

echo ">>> namespace and config"
render k8s/01-*.yaml | kubectl apply -f -
render k8s/03-*.yaml | kubectl apply -f -
render k8s/04-*.yaml | kubectl apply -f -

if ! kubectl -n "$NS" get secret shop-secrets >/dev/null 2>&1; then
  echo "Secret shop-secrets is missing. Fill every CHANGE_ME in k8s/02-*.yaml, then kubectl apply -f k8s/02-*.yaml" >&2
  exit 1
fi

echo ">>> schema jobs"
kubectl -n "$NS" delete job backend-migrate order-migrate --ignore-not-found --wait=true
for f in k8s/05-*.yaml k8s/06-*.yaml; do render "$f" | kubectl apply -f -; done
if ! kubectl -n "$NS" wait --for=condition=complete job/backend-migrate job/order-migrate --timeout=300s; then
  echo "Migration failed. Logs:" >&2
  kubectl -n "$NS" logs job/backend-migrate --tail=50 >&2 || true
  kubectl -n "$NS" logs job/order-migrate --tail=50 >&2 || true
  exit 1
fi

echo ">>> services"
for f in k8s/1[0-2]-*.yaml k8s/0[7-9]-*.yaml k8s/1[3-9]-*.yaml k8s/2[0-4]-*.yaml; do
  case "$f" in k8s/14-*) grep -q PUT-CERTIFICATE-ID-HERE "$f" && continue ;; esac
  case "$f" in k8s/2[1-4]-*) [ "${APPLY_NETPOL:-false}" = true ] || continue ;; esac
  render "$f" | kubectl apply -f -
done

for d in order-service backend-api frontend; do
  kubectl -n "$NS" rollout status deployment/"$d" --timeout=300s
done

echo ">>> dependency check from inside the cluster"
kubectl -n "$NS" exec deploy/backend-api -- python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz/deps').read().decode())"
kubectl -n "$NS" exec deploy/order-service -- python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz/deps').read().decode())"
echo "Done: https://${DOMAIN}"
