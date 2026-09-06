"""Abstract cloud provider interface for managing Node VMs."""

from __future__ import annotations

import abc
import logging

logger = logging.getLogger(__name__)


class CloudProvider(abc.ABC):
    @abc.abstractmethod
    def start_instance(self, instance_id: str) -> None:
        """Start the physical cloud instance."""
        pass

    @abc.abstractmethod
    def stop_instance(self, instance_id: str) -> None:
        """Stop the physical cloud instance."""
        pass


def get_cloud_provider(provider_name: str) -> CloudProvider:
    """Factory to get the correct CloudProvider implementation."""
    provider_name = provider_name.strip().lower()
    if provider_name == "aws_ec2":
        from blockhost_backend.services.cloud.aws_ec2 import AwsEc2Provider
        return AwsEc2Provider()
    
    raise NotImplementedError(f"Cloud provider {provider_name} is not supported.")
