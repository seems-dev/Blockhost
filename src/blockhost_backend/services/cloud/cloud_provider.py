"""Abstract cloud provider interface and factory for managing Node VMs."""

from __future__ import annotations

import abc
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CloudProvider(abc.ABC):
    
    @abc.abstractmethod
    def create_instance(self, region: str, instance_type: str, image_id: str, ssh_key: Optional[str] = None, node_token: Optional[str] = None) -> str:
        """Create a new compute instance and return its ID."""
        pass

    @abc.abstractmethod
    def start_instance(self, instance_id: str) -> None:
        """Start the physical cloud instance."""
        pass

    @abc.abstractmethod
    def stop_instance(self, instance_id: str) -> None:
        """Stop the physical cloud instance."""
        pass
        
    @abc.abstractmethod
    def terminate_instance(self, instance_id: str) -> None:
        """Terminate and permanently delete the cloud instance."""
        pass

    @abc.abstractmethod
    def get_instance_ip(self, instance_id: str) -> str:
        """Get the public IP of the instance."""
        pass


def get_cloud_provider(provider_name: str) -> CloudProvider:
    """Factory to get the correct CloudProvider implementation."""
    provider_name = provider_name.strip().lower()
    
    if provider_name == "aws" or provider_name == "aws_ec2":
        from blockhost_backend.services.cloud.aws_provider import AwsProvider
        return AwsProvider()
    elif provider_name == "hetzner":
        from blockhost_backend.services.cloud.hetzner_provider import HetznerProvider
        return HetznerProvider()
    elif provider_name == "contabo":
        from blockhost_backend.services.cloud.contabo_provider import ContaboProvider
        return ContaboProvider()
    
    raise NotImplementedError(f"Cloud provider {provider_name} is not supported.")
