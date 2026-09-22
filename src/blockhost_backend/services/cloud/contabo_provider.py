"""Contabo Cloud provider implementation."""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.cloud.cloud_provider import CloudProvider

logger = logging.getLogger(__name__)

class ContaboProvider(CloudProvider):
    def __init__(self):
        settings = get_settings()
        self.client_id = getattr(settings, "contabo_client_id", None)
        self.client_secret = getattr(settings, "contabo_client_secret", None)
        self.api_password = getattr(settings, "contabo_api_password", None)
        self.api_user = getattr(settings, "contabo_api_user", None)
        
        if not all([self.client_id, self.client_secret, self.api_password, self.api_user]):
            logger.warning("Contabo API credentials are not fully configured.")
            
        self.base_url = "https://api.contabo.com/v1"
        self._token = None

    def _get_auth_headers(self) -> dict:
        if not self._token:
            # Exchange credentials for an OAuth2 token
            with httpx.Client() as client:
                resp = client.post(
                    "https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token",
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "password",
                        "username": self.api_user,
                        "password": self.api_password,
                    }
                )
                resp.raise_for_status()
                self._token = resp.json()["access_token"]
                
        return {
            "Authorization": f"Bearer {self._token}",
            "x-request-id": "blockhost-req",
            "Content-Type": "application/json"
        }

    def create_instance(self, region: str, instance_type: str, image_id: str, ssh_key: Optional[str] = None, node_token: Optional[str] = None) -> str:
        logger.info("Creating Contabo instance in %s of type %s", region, instance_type)
        payload = {
            "region": region,
            "productId": instance_type,
            "imageId": image_id,
        }
        if ssh_key:
            payload["sshKeys"] = [ssh_key]
        if node_token:
            from blockhost_backend.services.cloud.cloud_init import generate_cloud_init
            payload["userData"] = generate_cloud_init(node_token)
            
        with httpx.Client() as client:
            resp = client.post(f"{self.base_url}/compute/instances", headers=self._get_auth_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
            instance_id = str(data["data"][0]["instanceId"])
            logger.info("Successfully created Contabo instance: %s", instance_id)
            return instance_id

    def start_instance(self, instance_id: str) -> None:
        logger.info("Starting Contabo instance: %s", instance_id)
        with httpx.Client() as client:
            resp = client.post(f"{self.base_url}/compute/instances/{instance_id}/actions/start", headers=self._get_auth_headers())
            resp.raise_for_status()

    def stop_instance(self, instance_id: str) -> None:
        logger.info("Stopping Contabo instance: %s", instance_id)
        with httpx.Client() as client:
            resp = client.post(f"{self.base_url}/compute/instances/{instance_id}/actions/stop", headers=self._get_auth_headers())
            resp.raise_for_status()
            
    def terminate_instance(self, instance_id: str) -> None:
        logger.info("Terminating Contabo instance: %s", instance_id)
        with httpx.Client() as client:
            resp = client.delete(f"{self.base_url}/compute/instances/{instance_id}", headers=self._get_auth_headers())
            resp.raise_for_status()

    def get_instance_ip(self, instance_id: str) -> str:
        with httpx.Client() as client:
            resp = client.get(f"{self.base_url}/compute/instances/{instance_id}", headers=self._get_auth_headers())
            resp.raise_for_status()
            data = resp.json()
            ip = data["data"][0]["ipConfig"]["v4"]["ip"]
            if not ip:
                raise ValueError("Instance does not have a public IPv4 address yet.")
            return ip
