"""AWS EC2 provider implementation."""

from __future__ import annotations

import logging

import boto3

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.cloud.cloud_provider import CloudProvider

logger = logging.getLogger(__name__)


class AwsEc2Provider(CloudProvider):
    def __init__(self):
        settings = get_settings()
        self.ec2 = boto3.client(
            "ec2",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )

    def start_instance(self, instance_id: str) -> None:
        """Start the EC2 instance."""
        try:
            logger.info("Starting AWS EC2 instance: %s", instance_id)
            self.ec2.start_instances(InstanceIds=[instance_id])
        except Exception as e:
            logger.error("Failed to start EC2 instance %s: %s", instance_id, e)
            raise

    def stop_instance(self, instance_id: str) -> None:
        """Stop the EC2 instance."""
        try:
            logger.info("Stopping AWS EC2 instance: %s", instance_id)
            self.ec2.stop_instances(InstanceIds=[instance_id])
        except Exception as e:
            logger.error("Failed to stop EC2 instance %s: %s", instance_id, e)
            raise
