"""Hetzner Cloud provider implementation."""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.cloud.cloud_provider import CloudProvider

logger = logging.getLogger(__name__)

class HetznerProvider(CloudProvider):
    def __init__(self):
        settings = get_settings()
        self.api_key = getattr(settings, "hetzner_api_key", None)
        if not self.api_key:
            logger.warning("Hetzner API key is not configured.")
        self.base_url = "https://api.hetzner.cloud/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def create_instance(self, region: str, instance_type: str, image_id: str, ssh_key: Optional[str] = None, node_token: Optional[str] = None) -> str:
        logger.info("Creating Hetzner instance in %s of type %s", region, instance_type)
        payload = {
            "name": f"blockhost-node-{region}-{instance_type}",
            "server_type": instance_type,
            "location": region,
            "image": image_id,
        }
        if ssh_key:
            payload["ssh_keys"] = [ssh_key]
        if node_token:
            from blockhost_backend.services.cloud.cloud_init import generate_cloud_init
            payload["user_data"] = generate_cloud_init(node_token)
            
            
        with httpx.Client() as client:
            resp = client.post(f"{self.base_url}/servers", headers=self.headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            instance_id = str(data["server"]["id"])
            logger.info("Successfully created Hetzner instance: %s", instance_id)
            return instance_id

    def start_instance(self, instance_id: str) -> None:
        logger.info("Starting Hetzner instance: %s", instance_id)
        with httpx.Client() as client:
            resp = client.post(f"{self.base_url}/servers/{instance_id}/actions/poweron", headers=self.headers)
            resp.raise_for_status()

    def stop_instance(self, instance_id: str) -> None:
        logger.info("Stopping Hetzner instance: %s", instance_id)
        with httpx.Client() as client:
            resp = client.post(f"{self.base_url}/servers/{instance_id}/actions/poweroff", headers=self.headers)
            resp.raise_for_status()
            
    def terminate_instance(self, instance_id: str) -> None:
        logger.info("Terminating Hetzner instance: %s", instance_id)
        with httpx.Client() as client:
            resp = client.delete(f"{self.base_url}/servers/{instance_id}", headers=self.headers)
            resp.raise_for_status()

    def get_instance_ip(self, instance_id: str) -> str:
        with httpx.Client() as client:
            resp = client.get(f"{self.base_url}/servers/{instance_id}", headers=self.headers)
            resp.raise_for_status()
            data = resp.json()
            ip = data["server"]["public_net"]["ipv4"]["ip"]
            if not ip:
                raise ValueError("Instance does not have a public IPv4 address yet.")
            return ip
