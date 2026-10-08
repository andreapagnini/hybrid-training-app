#!/usr/bin/env bash
# Deploy (or update) the private helper on Google Cloud Run.
# Run it in Google Cloud Shell from the repository's root folder:
#   bash helper/deploy.sh YOUR_PROJECT_ID
# Safe to run again: it only creates what is missing, then deploys the new code.
set -euo pipefail

PROJECT="${1:?Usage: bash helper/deploy.sh PROJECT_ID [REGION]}"
REGION="${2:-europe-west1}"
SERVICE="hybrid-training-helper"
BUCKET="${PROJECT}-garmin-helper"
SECRET="helper-app-key"
SA_NAME="hybrid-training-helper"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
ORIGINS="https://andreapagnini.github.io"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/6 Project and services"
gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com storage.googleapis.com iam.googleapis.com

say "2/6 Private storage for the Garmin sign-in (bucket $BUCKET, $REGION)"
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi

say "3/6 Helper identity with access to that bucket only"
if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name="Hybrid Training helper"
fi
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role="roles/storage.objectAdmin" >/dev/null
# Newer projects need this so "deploy from source" may build the container.
NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/run.builder" --condition=None >/dev/null

say "4/6 Secret app key"
if ! gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(36), end="")' | \
    gcloud secrets create "$SECRET" --replication-policy=user-managed --locations="$REGION" --data-file=-
fi
gcloud secrets add-iam-policy-binding "$SECRET" \
  --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor" >/dev/null

say "5/6 Build and deploy (takes a few minutes)"
rm -rf "$HERE/_app" && mkdir -p "$HERE/_app"
cp "$ROOT/index.html" "$ROOT/garmin.js" "$ROOT/sw.js" "$ROOT/manifest.webmanifest" "$HERE/_app/"
cp -r "$ROOT/icons" "$HERE/_app/"
[ -d "$ROOT/media" ] && cp -r "$ROOT/media" "$HERE/_app/"
gcloud run deploy "$SERVICE" --source "$HERE" --region "$REGION" \
  --service-account "$SA" \
  --set-secrets "APP_KEY=${SECRET}:latest" \
  --set-env-vars "STORE_URL=gs://${BUCKET},ALLOWED_ORIGINS=${ORIGINS}" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 1 --concurrency 10 \
  --cpu 1 --memory 512Mi --timeout 300 --quiet
rm -rf "$HERE/_app"

say "6/6 Keep only the 2 latest builds (stays inside the free storage)"
cat > /tmp/helper-cleanup.json <<'JSON'
[{"name":"keep-latest","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":2}},
 {"name":"delete-old","action":{"type":"Delete"},"condition":{"tagState":"any","olderThan":"1d"}}]
JSON
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --location="$REGION" --policy=/tmp/helper-cleanup.json --no-dry-run --quiet >/dev/null 2>&1 || true

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
KEY="$(gcloud secrets versions access latest --secret="$SECRET")"
CODE="$(python3 -c 'import base64,json,sys; print(base64.urlsafe_b64encode(json.dumps({"u":sys.argv[1],"k":sys.argv[2]}).encode()).decode().rstrip("="))' "$URL" "$KEY")"

say "Done."
echo "Helper address:     $URL"
echo "Test copy of app:   $URL/"
echo
echo "Setup code (treat it like a password, paste it in the app: Settings > Garmin > Setup code):"
echo "$CODE"
