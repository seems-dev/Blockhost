#!/usr/bin/env bash
# =============================================================================
# BlockHost — Control Plane EC2 Setup Script
# Run this ONCE on a fresh Ubuntu 24.04 EC2 to get it ready for deployment.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/.../setup-control-plane.sh | bash
#   -- OR --
#   scp deploy/setup-control-plane.sh ubuntu@<EC2_IP>:~/
#   ssh ubuntu@<EC2_IP> 'bash ~/setup-control-plane.sh'
# =============================================================================
set -euo pipefail

echo "======================================================"
echo " BlockHost Control Plane — First-time Setup"
echo "======================================================"

# --- 1. System dependencies ---
echo "[1/6] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    git \
    curl \
    docker.io \
    docker-compose-plugin

sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ubuntu

echo "[2/6] Docker installed: $(docker --version)"

# --- 2. Clone the repository ---
echo "[2/6] Cloning blockhost repository..."
sudo mkdir -p /opt/blockhost
sudo chown ubuntu:ubuntu /opt/blockhost
git clone https://github.com/YOUR_GITHUB_USERNAME/blockhost.git /opt/blockhost

# --- 3. Create the .env file ---
echo "[3/6] Setting up environment file..."
cd /opt/blockhost/deploy
if [ ! -f .env ]; then
    cp env.production.example .env
    echo ""
    echo "⚠️  IMPORTANT: Edit /opt/blockhost/deploy/.env with your real secrets!"
    echo "   nano /opt/blockhost/deploy/.env"
    echo ""
    echo "   Required values to fill in:"
    echo "   - DOMAIN=your-api-domain.com"
    echo "   - POSTGRES_PASSWORD=<strong-password>"
    echo "   - JWT_SECRET_KEY=<run: openssl rand -hex 32>"
    echo "   - WORKER_AGENT_TOKEN=<run: openssl rand -hex 32>"
    echo "   - MINECRAFT_PUBLIC_HOST=<this EC2's public IP or domain>"
    echo ""
fi

# --- 4. Set up SSH for GitHub Actions deployment ---
echo "[4/6] Creating deployment user SSH setup..."
mkdir -p ~/.ssh
chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
echo ""
echo "ℹ️  Add your GitHub Actions deploy key public key to ~/.ssh/authorized_keys"
echo "   If you haven't generated one yet:"
echo "   ssh-keygen -t ed25519 -C 'github-actions-blockhost' -f ~/.ssh/blockhost_deploy"
echo "   Then add ~/.ssh/blockhost_deploy.pub content to authorized_keys"
echo "   And add ~/.ssh/blockhost_deploy (private key) as CONTROL_PLANE_SSH_KEY secret in GitHub"
echo ""

# --- 5. Start the stack ---
echo "[5/6] Starting BlockHost stack (docker compose)..."
echo "      (Run this step manually AFTER editing .env)"
echo "      cd /opt/blockhost/deploy && docker compose -f docker-compose.prod.yml up -d"

# --- 6. Done ---
echo ""
echo "======================================================"
echo " ✅ Control Plane setup complete!"
echo "======================================================"
echo ""
echo " Next steps:"
echo " 1. Edit /opt/blockhost/deploy/.env with your real secrets"
echo " 2. cd /opt/blockhost/deploy && docker compose -f docker-compose.prod.yml up -d"
echo " 3. Add GitHub Secrets to your repo:"
echo "    - CONTROL_PLANE_HOST = $(curl -s ifconfig.me)"
echo "    - CONTROL_PLANE_SSH_KEY = (contents of your deploy private key)"
echo " 4. Set up your Agent Node(s) using deploy/setup-agent-node.sh"
echo ""
