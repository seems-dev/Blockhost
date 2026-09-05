#!/bin/bash
set -e
AGENT_IP="ec2-32-236-113-30.ap-southeast-2.compute.amazonaws.com"
CP_IP="ec2-52-63-135-144.ap-southeast-2.compute.amazonaws.com"
AGENT_KEY="$HOME/Downloads/agent-node-key.pem"
CP_KEY="$HOME/Downloads/blockhost-key.pem"

echo "Deploying full backend to Control Plane Node..."
# Sync the entire src and alembic directories to the Control Plane
rsync -avz -e "ssh -o StrictHostKeyChecking=no -i $CP_KEY" /home/seems/blockhost/src ubuntu@$CP_IP:/opt/blockhost/
rsync -avz -e "ssh -o StrictHostKeyChecking=no -i $CP_KEY" /home/seems/blockhost/alembic ubuntu@$CP_IP:/opt/blockhost/
rsync -avz -e "ssh -o StrictHostKeyChecking=no -i $CP_KEY" /home/seems/blockhost/alembic.ini ubuntu@$CP_IP:/opt/blockhost/

# Rebuild and restart the containers, then apply database migrations
ssh -o StrictHostKeyChecking=no -i $CP_KEY ubuntu@$CP_IP << 'EOF'
cd /opt/blockhost/deploy
sudo docker compose -f docker-compose.prod.yml up -d --build api worker
sudo docker exec deploy-api-1 alembic upgrade head
EOF

echo "Done! The Control Plane is fully updated with the latest code and database schema."
