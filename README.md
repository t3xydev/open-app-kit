# F-Droid Repo Server

Portable, env-driven F-Droid repository. Runs as a long-lived Docker service with a **single data volume**, generates the repo locally, optionally deploys to S3-compatible storage, and accepts APKs via authenticated webhooks.

## Modes

| Mode | When | Client `REPO_URL` | Behavior |
|------|------|-------------------|----------|
| **Self-host** | `SELF_HOST=true` **or** S3 vars incomplete | `http://host:8000/fdroid/repo` | Generate + serve `data/repo/` |
| **S3 primary** | All `S3_*` set and `SELF_HOST` not true | Public bucket/R2 URL | Ingest → build → `fdroid deploy` |
| **Both** | `SELF_HOST=true` + S3 configured | Local `/fdroid/repo` | Self-host primary + S3 mirror |

Default: `SELF_HOST=true` with blank S3 keys → self-host only.

## Quick start (Docker)

```bash
cp .env.example .env
# Set WEBHOOK_TOKEN, REPO_URL, and optionally S3_* / SELF_HOST

mkdir -p data
docker compose up --build -d
```

- API / health: `http://localhost:8000/health`
- Status: `http://localhost:8000/api/status`
- Repo (self-host): `http://localhost:8000/fdroid/repo/`

One volume only: `./data:/data` (apks, metadata, repo, keystore, configs, status).

### Webhooks

```bash
# Upload an APK and rebuild
curl -X POST "http://localhost:8000/hooks/apk" \
  -H "Authorization: Bearer $WEBHOOK_TOKEN" \
  -F "file=@app-release.apk"

# Rebuild / deploy without a new APK
curl -X POST "http://localhost:8000/hooks/publish" \
  -H "Authorization: Bearer $WEBHOOK_TOKEN"
```

#### EAS Expo ingest

Point an EAS Build webhook at `POST /hooks/eas`. Finished Android APK builds for `production`, `preview`, or `development`/`dev` are saved as `{displayName}_{production|preview|dev}.apk` (EAS `metadata.appName` / app.json `name`) and published.

```bash
eas webhook:create \
  --event BUILD \
  --url "https://YOUR_HOST/hooks/eas" \
  --secret "$EAS_WEBHOOK_SECRET"
```

Use the same value for `EAS_WEBHOOK_SECRET` (or `WEBHOOK_TOKEN`) on the server. EAS profiles must produce APKs (`android.buildType: "apk"`), not AABs.

### One-shot full publish

```bash
docker compose --profile publish run --rm publish
```

## Deploy on Railway

[`railway.toml`](railway.toml) builds from the Dockerfile and starts via [`scripts/start.sh`](scripts/start.sh) (same as local Docker). On every boot it **re-syncs `config.yml` from env**. If APKs exist and env-driven settings changed, it rebuilds the index **in the background** (so a failed rebuild cannot crash the service). APKs under `/data/apks` are never cleared.

1. Create a service from this repo (Railway will pick up the Dockerfile).
2. **Attach a volume** with mount path **`/data`** (required for APKs, keystore, and generated repo):
   ```bash
   railway volume add --mount-path /data
   ```
3. Set variables (Variables tab), at least:
   - `WEBHOOK_TOKEN` — webhook auth
   - `SELF_HOST=true` — serve repo from this service (or leave S3 blank)
   - `REPO_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}/fdroid/repo`
   - `REPO_NAME`, signing fields as needed
   - Optional `S3_*` for mirror / S3-primary mode
4. Deploy. Repo (self-host): `https://<domain>/fdroid/repo/`

`DATA_DIR` defaults to `/data` in the image; keep the volume mount at that path.
## Bare metal

Requires `fdroidserver`, `rclone`, Android SDK build-tools (`apksigner`), and Java 17+.

```bash
cp .env.example .env
# optional: export DATA_DIR=./data

./scripts/init.sh
./scripts/update.sh      # incremental build + optional S3 deploy
# or
./scripts/publish.sh     # clean + rebuild + optional S3 deploy
```

Drop signed `.apk` files into `$DATA_DIR/apks/` (default: `./apks` when `DATA_DIR` is unset).

## Data layout (`DATA_DIR`)

```
data/
├── apks/              # input APKs (webhook or manual drop)
├── metadata/          # fdroid per-app YAML
├── repo/              # generated index + APKs (served at /fdroid/repo)
├── tmp/ cache/        # fdroid scratch
├── keystore.p12       # repo signing key
├── config.yml         # generated
├── rclone.conf        # generated when S3 is set
├── assets/            # optional icon override
├── .keystore_pass     # auto-generated signing passwords
└── status.json        # last webhook publish status
```

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/init.sh` | Generate `config.yml` / `rclone.conf` from env |
| `scripts/build.sh` | Copy APKs, verify, `fdroid update` |
| `scripts/deploy.sh` | `fdroid deploy` if S3 configured; else succeed (self-host) |
| `scripts/update.sh` | Incremental init + build + deploy (webhooks) |
| `scripts/publish.sh` | Clean + full rebuild + deploy |
| `scripts/clean.sh` | Wipe generated state under `DATA_DIR` |
| `scripts/gensecret.sh` | Generate keystore passwords |
| `scripts/verify.sh` | Verify APK signatures |
| `scripts/keygen.sh` | Create signing keystore |

## Environment

See [`.env.example`](.env.example):

- **Mode**: `SELF_HOST`, `DATA_DIR`
- **Repo**: `REPO_NAME`, `REPO_URL`, `REPO_DESCRIPTION`, `REPO_WEB_BASE_URL`
- **Signing**: `KEYSTORE_FILE`, `REPO_KEYALIAS`, `KEYSTORE_PASS`, `KEY_PASS`, `KEYDNAME`
- **Webhooks**: `WEBHOOK_TOKEN`, optional `EAS_WEBHOOK_SECRET` for `/hooks/eas`
- **Notifications**: `APPRISE_CONFIG_URL` — Apprise [configuration URL](https://github.com/caronc/apprise/wiki/config) for publish success/failure alerts (leave blank to disable). Recommended: `/data/apprise.yml` on the volume with your service URLs (`discord://…`, etc.).
- **S3**: `S3_REMOTE_NAME`, `S3_PROVIDER`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_ENDPOINT`
