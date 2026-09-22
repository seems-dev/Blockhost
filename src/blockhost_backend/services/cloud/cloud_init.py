import base64
from blockhost_backend.config.config_manager import get_settings

def generate_cloud_init(node_token: str) -> str:
    settings = get_settings()
    
    # We need the WS URL for the agent to connect back to us
    # Assuming public_api_url is configured, e.g. https://api.blockhost.com
    # or http://192.168.1.100:8000
    base_url = settings.public_api_url or "http://localhost:8000"
    ws_url = base_url.replace('http', 'ws').rstrip('/') + "/api/nodes/ws"
    
    script = f"""#!/bin/bash
set -e

# Update and install dependencies
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y apt-transport-https ca-certificates curl software-properties-common git jq build-essential libffi-dev libssl-dev

# Install Docker
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
fi

# Install Python 3.12
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y python3.12 python3.12-venv python3.12-dev

# Clone Blockhost Agent
rm -rf /opt/blockhost
git clone -b v1.0.0 --depth 1 https://github.com/seems-dev/Blockhost.git /opt/blockhost

# Setup virtual environment
cd /opt/blockhost/src
python3.12 -m venv /opt/blockhost/venv
/opt/blockhost/venv/bin/pip install fastapi uvicorn websockets psutil httpx docker

# We might also need to install the project if it has a setup.py or just use PYTHONPATH
# Create systemd service
cat > /etc/systemd/system/blockhost-agent.service << 'EOF'
[Unit]
Description=BlockHost Agent
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/blockhost/src
Environment="AGENT_TOKEN={node_token}"
Environment="CONTROL_AGENT_TOKEN={settings.worker_agent_token}"
Environment="CONTROLLER_WS_URL={ws_url}"
Environment="NODE_NAME=$(hostname)"
Environment="PYTHONPATH=/opt/blockhost/src"
ExecStart=/opt/blockhost/venv/bin/python3.12 -m uvicorn blockhost_agent.agent:app --host 0.0.0.0 --port 9000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now blockhost-agent.service
"""

    encoded_script = base64.b64encode(script.encode('utf-8')).decode('utf-8')

    cloud_config = f"""#cloud-config
write_files:
  - path: /opt/blockhost_setup.sh
    permissions: '0755'
    encoding: b64
    content: {encoded_script}
runcmd:
  - /opt/blockhost_setup.sh
"""
    return cloud_config
