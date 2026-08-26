#!/usr/bin/env bash
# =============================================================================
# BlockHost — Agent Node EC2 Setup Script
# Run this ONCE on each fresh Ubuntu 24.04 EC2 you want to use as a game node.
#
# Usage:
#   scp deploy/setup-agent-node.sh ubuntu@<AGENT_EC2_IP>:~/
#   ssh ubuntu@<AGENT_EC2_IP> 'bash ~/setup-agent-node.sh'
# =============================================================================
set -euo pipefail

echo "======================================================"
echo " BlockHost Agent Node — First-time Setup"
echo "======================================================"

# --- 1. System dependencies ---
echo "[1/6] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    git \
    curl \
    python3 \
    python3-pip \
    python3-venv \
    libcap2-bin   # needed to allow non-root to bind ports

echo "[1/6] System packages installed."

# --- 2. Clone the repository ---
echo "[2/6] Cloning blockhost repository..."
sudo mkdir -p /opt/blockhost
sudo chown ubuntu:ubuntu /opt/blockhost
git clone https://github.com/YOUR_GITHUB_USERNAME/blockhost.git /opt/blockhost

# --- 3. Install Python dependencies ---
echo "[3/6] Installing Python package..."
cd /opt/blockhost
pip install -e . --quiet

echo "[3/6] Python deps installed."

# --- 4. Create the agent environment file ---
echo "[4/6] Creating agent environment config..."
if [ ! -f /etc/blockhost-agent.env ]; then
    sudo tee /etc/blockhost-agent.env > /dev/null <<'EOF'
# BlockHost Agent Node Environment
# Edit this file with your real values.

# Token used by this agent to register/heartbeat with the control plane.
# If you pre-register the node, use the one-time agent_token returned by /api/nodes/register.
AGENT_TOKEN=REPLACE_WITH_YOUR_SHARED_SECRET_TOKEN

# Token accepted for control-plane HTTP actions (provision/start/stop/files).
# MUST match WORKER_AGENT_TOKEN in the Control Plane's deploy/.env.
CONTROL_AGENT_TOKEN=REPLACE_WITH_CONTROL_PLANE_WORKER_AGENT_TOKEN

# A human-readable name for this node (shown in admin panel)
NODE_NAME=node-1

# This node's PUBLIC IP address (so the Control Plane knows where to send players)
NODE_PUBLIC_IP=REPLACE_WITH_THIS_EC2_PUBLIC_IP

# HTTP port that the agent listens on. Must match blockhost-agent.service.
AGENT_PORT=9000

# URL of the Control Plane API (used by agent to register and send heartbeats)
CONTROLLER_WS_URL=ws://REPLACE_WITH_CONTROL_PLANE_IP:80/api/nodes/ws

# Where Minecraft server files are stored on this machine
SERVERS_ROOT_DIR=/opt/blockhost/agent_storage/servers
VERSIONS_ROOT_DIR=/opt/blockhost/agent_storage/versions
EOF
    sudo chmod 600 /etc/blockhost-agent.env
    echo ""
    echo "⚠️  IMPORTANT: Edit /etc/blockhost-agent.env with your real values!"
    echo "   sudo nano /etc/blockhost-agent.env"
    echo ""
fi

# --- 5. Install the systemd service ---
echo "[5/6] Installing blockhost-agent systemd service..."
sudo cp /opt/blockhost/deploy/blockhost-agent.service /etc/systemd/system/blockhost-agent.service
sudo systemctl daemon-reload
sudo systemctl enable blockhost-agent
echo "[5/6] Service installed and enabled to start on boot."

# --- 6. Create storage directories ---
echo "[6/6] Creating storage directories..."
mkdir -p /opt/blockhost/agent_storage/servers
mkdir -p /opt/blockhost/agent_storage/versions
mkdir -p /opt/blockhost/agent_storage/jdks

# --- Done ---
echo ""
echo "======================================================"
echo " ✅ Agent Node setup complete!"
echo "======================================================"
echo ""
echo " Next steps:"
echo " 1. Edit the config:   sudo nano /etc/blockhost-agent.env"
echo "    - Set AGENT_TOKEN to the node registration token (or WORKER_AGENT_TOKEN if auto-registering)"
echo "    - Set CONTROL_AGENT_TOKEN to match your Control Plane's WORKER_AGENT_TOKEN"
echo "    - Set NODE_PUBLIC_IP to: $(curl -s ifconfig.me)"
echo "    - Set CONTROLLER_WS_URL to point to your Control Plane"
echo ""
echo " 2. Start the agent:   sudo systemctl start blockhost-agent"
echo " 3. Check it's running: sudo systemctl status blockhost-agent"
echo " 4. Watch the logs:    sudo journalctl -u blockhost-agent -f"
echo ""
echo " 5. Add GitHub Secrets to your repo:"
echo "    - AGENT_HOST_1 = $(curl -s ifconfig.me)"
echo "    - AGENT_SSH_KEY = (contents of your deploy private key)"
echo ""
echo " 6. In the BlockHost Admin Panel, click 'Add Node' and enter:"
echo "    - IP Address: $(curl -s ifconfig.me)"
echo "    - Port: 9000"
echo "    - Token: (same as AGENT_TOKEN above)"
echo ""
