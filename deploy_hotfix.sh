#!/bin/bash
set -e
AGENT_IP="ec2-32-236-113-30.ap-southeast-2.compute.amazonaws.com"
CP_IP="ec2-52-63-135-144.ap-southeast-2.compute.amazonaws.com"
AGENT_KEY="$HOME/Downloads/agent-node-key.pem"
CP_KEY="$HOME/Downloads/blockhost-key.pem"

echo "Deploying to Agent Node..."
scp -o StrictHostKeyChecking=no -i $AGENT_KEY /home/seems/blockhost/src/blockhost_agent/agent.py ubuntu@$AGENT_IP:/opt/blockhost/src/blockhost_agent/agent.py
scp -o StrictHostKeyChecking=no -i $AGENT_KEY /home/seems/blockhost/src/blockhost_backend/runtime/systemd_runtime.py ubuntu@$AGENT_IP:/opt/blockhost/src/blockhost_backend/runtime/systemd_runtime.py
scp -o StrictHostKeyChecking=no -i $AGENT_KEY /home/seems/blockhost/src/blockhost_backend/minecraft/rcon_client.py ubuntu@$AGENT_IP:/opt/blockhost/src/blockhost_backend/minecraft/rcon_client.py
scp -o StrictHostKeyChecking=no -i $AGENT_KEY /home/seems/blockhost/src/blockhost_backend/minecraft/java_properties.py ubuntu@$AGENT_IP:/opt/blockhost/src/blockhost_backend/minecraft/java_properties.py
scp -o StrictHostKeyChecking=no -i $AGENT_KEY /home/seems/blockhost/src/blockhost_backend/minecraft/bedrock_properties.py ubuntu@$AGENT_IP:/opt/blockhost/src/blockhost_backend/minecraft/bedrock_properties.py
ssh -o StrictHostKeyChecking=no -i $AGENT_KEY ubuntu@$AGENT_IP "sudo systemctl restart blockhost-agent"

echo "Deploying to Control Plane Node..."
scp -o StrictHostKeyChecking=no -i $CP_KEY /home/seems/blockhost/src/blockhost_backend/api/servers.py ubuntu@$CP_IP:/opt/blockhost/src/blockhost_backend/api/servers.py
scp -o StrictHostKeyChecking=no -i $CP_KEY /home/seems/blockhost/src/blockhost_backend/minecraft/java_properties.py ubuntu@$CP_IP:/opt/blockhost/src/blockhost_backend/minecraft/java_properties.py
scp -o StrictHostKeyChecking=no -i $CP_KEY /home/seems/blockhost/src/blockhost_backend/minecraft/bedrock_properties.py ubuntu@$CP_IP:/opt/blockhost/src/blockhost_backend/minecraft/bedrock_properties.py
ssh -o StrictHostKeyChecking=no -i $CP_KEY ubuntu@$CP_IP "cd /opt/blockhost && sudo docker compose restart api worker"

echo "Done!"
