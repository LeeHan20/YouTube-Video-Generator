# EC2 CI/CD

This repository deploys to EC2 from GitHub Actions on every push to `master`.

## GitHub Secrets

Add these in GitHub repository settings under `Secrets and variables` -> `Actions`.

- `EC2_HOST`: EC2 public IP or domain, for example `13.209.72.50`
- `EC2_USER`: SSH user, normally `ubuntu`
- `EC2_SSH_KEY`: private key content for the EC2 key pair

Optional repository variable:

- `EC2_APP_DIR`: deployment directory, defaults to `/home/ubuntu/YouTube-Video-Generator`

## Server Files Preserved By Deploy

The workflow syncs code with `rsync --delete`, but keeps these server-only paths untouched:

- `.env`
- `.venv/`
- `encrypted_tokens/`
- `storage/`
- `service-account.json`
- `client_secret.json`

## First-Time Server Setup

The workflow runs `scripts/deploy_ec2.sh` with `INSTALL_SYSTEMD=1`, which installs and restarts:

- `youtube-video-generator.service`

The worker service template is included but is not enabled by default because it can run scheduled automation and publish checks. Enable it manually when ready:

```bash
cd /home/ubuntu/YouTube-Video-Generator
INSTALL_SYSTEMD=1 INSTALL_WORKER=1 ./scripts/deploy_ec2.sh
sudo systemctl enable --now youtube-video-generator-worker.service
```
