#!/usr/bin/env sh
# Creates the files docker-compose.yml mounts as secrets:
#   secrets/master.key  credential-store master key. Back it up somewhere safe and separate from
#                       database backups: without it, stored device credentials cannot be decrypted.
#   secrets/s3.json     access keys for the bundled object store, from S3_ACCESS_KEY / S3_SECRET_KEY in .env
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "create .env first (cp .env.example .env)"; exit 1; }
mkdir -p secrets
mkdir -p secrets/tls
umask 077

if [ -s secrets/master.key ]; then
  echo "secrets/master.key exists; leaving it alone"
else
  python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())" > secrets/master.key
  echo "wrote secrets/master.key"
fi

S3_ACCESS_KEY=$(grep -E '^S3_ACCESS_KEY=' .env | cut -d= -f2- || true)
S3_SECRET_KEY=$(grep -E '^S3_SECRET_KEY=' .env | cut -d= -f2- || true)
python3 - "${S3_ACCESS_KEY:-assurance}" "$S3_SECRET_KEY" > secrets/s3.json <<'PY'
import json, sys
key, secret = sys.argv[1], sys.argv[2]
if not secret:
    sys.exit("S3_SECRET_KEY is empty in .env")
print(json.dumps({"identities": [{"name": "assurance",
      "credentials": [{"accessKey": key, "secretKey": secret}],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}))
PY
echo "wrote secrets/s3.json"
# Containers run as non-root users and must be able to read the files.
chmod 0444 secrets/master.key secrets/s3.json
