#!/bin/bash
# Deploy the built V4 dashboard (site/index.html) to Cloudflare Pages by Direct Upload.
#
#   pages_deploy.sh check    no secrets: refuse unless the production address AND every other
#                            *.<project>.pages.dev address send an unauthenticated visitor to
#                            Cloudflare Access (owner-only login). Nothing is uploaded.
#   pages_deploy.sh deploy   needs CLOUDFLARE_API_TOKEN (Account > Cloudflare Pages > Edit) and
#                            CLOUDFLARE_ACCOUNT_ID. Reads the project's production branch from the
#                            Cloudflare API, uploads index.html + security headers to it with the
#                            pinned wrangler (deploy/cloudflare/package-lock.json), then confirms the
#                            deployment succeeded.
# The token is only ever passed in the environment; nothing here prints it.
set -euo pipefail
PROJECT="${CF_PAGES_PROJECT:-}"
[[ "$PROJECT" =~ ^[a-z0-9][a-z0-9-]{0,57}$ ]] || { echo "::error::repository variable CF_PAGES_PROJECT is missing or invalid"; exit 1; }
HOST="${PROJECT}.pages.dev"
CURL="${CURL:-curl}"

behind_access() {   # $1 = hostname; true only for a redirect to a *.cloudflareaccess.com login
  local out code loc
  out=$("$CURL" -sS -o /dev/null --max-time 20 -w '%{http_code} %{redirect_url}' "https://$1/" 2>/dev/null) || out="000 "
  code=${out%% *}; loc=${out#* }
  if [[ "$code" =~ ^30[1237]$ && "$loc" =~ ^https://[a-z0-9-]+\.cloudflareaccess\.com/ ]]; then
    echo "protected: $1 -> Cloudflare Access login"; return 0
  fi
  echo "::error::NOT protected: https://$1/ answered HTTP $code without an Access login"; return 1
}

check() {
  local ok=0 probe
  probe="$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n').${HOST}"     # any preview / per-deployment URL
  behind_access "$HOST" || ok=1
  behind_access "$probe" || ok=1
  if (( ok )); then
    echo "::error::Refusing to deploy private data. Protect BOTH ${HOST} and *.${HOST} with Cloudflare Access (see docs_v4/CLOUDFLARE_PAGES.md)."
    exit 1
  fi
}

api() {   # GET a Cloudflare API path; JSON on stdout
  "$CURL" -sS --max-time 30 -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/pages/projects/${PROJECT}$1"
}

deploy() {
  [[ -n "${CLOUDFLARE_API_TOKEN:-}" && -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]] || {
    echo "::error::secrets CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are required"; exit 1; }
  [[ -s site/index.html ]] || { echo "::error::site/index.html was not built"; exit 1; }
  local branch
  branch=$(api "" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if not d.get("success"):
    sys.exit("Cloudflare API: " + "; ".join(e.get("message", "?") for e in d.get("errors", [])))
r = d["result"]
src = (r.get("source") or {}).get("type") or "direct upload"
print("project %s: source=%s, production branch=%s" % (r["name"], src, r["production_branch"]), file=sys.stderr)
print(r["production_branch"])')
  rm -rf dist && mkdir dist
  cp site/index.html dist/index.html
  cp deploy/cloudflare/_headers dist/_headers
  printf 'User-agent: *\nDisallow: /\n' > dist/robots.txt
  if [[ -z "${WRANGLER:-}" ]]; then
    node -e 'process.exit(+process.versions.node.split(".")[0] >= 22 ? 0 : 1)' || {
      echo "::error::Node.js 22+ is required by the pinned wrangler"; exit 1; }
    npm ci --prefix deploy/cloudflare --ignore-scripts --no-audit --no-fund --loglevel=error
    WRANGLER=deploy/cloudflare/node_modules/.bin/wrangler
  fi
  "$WRANGLER" pages deploy dist --project-name "$PROJECT" --branch "$branch" \
    --commit-hash "${GITHUB_SHA:-unknown}" --commit-message "V4 dashboard build ${GITHUB_RUN_ID:-local}" --commit-dirty=true
  local i status
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    status=$(api "/deployments?env=production" | python3 -c '
import json, sys
d = json.load(sys.stdin)
dep = (d.get("result") or [{}])[0] or {}
st = dep.get("latest_stage") or {}
print("%s %s %s" % (st.get("status"), st.get("name"), dep.get("id")))')
    echo "latest production deployment: $status"
    case "$status" in
      success\ deploy*) return 0 ;;
      failure*|canceled*) echo "::error::Cloudflare reports the deployment failed"; exit 1 ;;
    esac
    sleep "${POLL_SECONDS:-5}"
  done
  echo "::error::deployment did not reach success in time"; exit 1
}

case "${1:-}" in
  check) check ;;
  deploy) deploy ;;
  *) echo "usage: $0 check|deploy"; exit 2 ;;
esac
