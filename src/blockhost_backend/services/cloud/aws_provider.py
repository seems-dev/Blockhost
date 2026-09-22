"""AWS EC2 provider implementation."""

from __future__ import annotations

import logging
from typing import Optional

import boto3

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.cloud.cloud_provider import CloudProvider

logger = logging.getLogger(__name__)


class AwsProvider(CloudProvider):
    def __init__(self):
        settings = get_settings()
        self.ec2 = boto3.client(
            "ec2",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )

    def create_instance(self, region: str, instance_type: str, image_id: str, ssh_key: Optional[str] = None, node_token: Optional[str] = None) -> str:
        """Provision a new AWS EC2 instance."""
        settings = get_settings()
        try:
            logger.info("Creating AWS EC2 instance in %s of type %s", region, instance_type)
            params = {
                "ImageId": image_id,
                "InstanceType": instance_type,
                "MinCount": 1,
                "MaxCount": 1,
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": [{"Key": "Name", "Value": "blockhost-node"}],
                    }
                ]
            }
            if ssh_key:
                params["KeyName"] = ssh_key
            if settings.aws_agent_sg_id:
                params["SecurityGroupIds"] = [settings.aws_agent_sg_id]
            if node_token:
                from blockhost_backend.services.cloud.cloud_init import generate_cloud_init
                params["UserData"] = generate_cloud_init(node_token)

            response = self.ec2.run_instances(**params)
            instance_id = response['Instances'][0]['InstanceId']
            logger.info("Successfully created AWS EC2 instance: %s", instance_id)
            return instance_id
        except Exception as e:
            logger.error("Failed to create EC2 instance: %s", e)
            raise

    def start_instance(self, instance_id: str) -> None:
        """Start the EC2 instance."""
        try:
            logger.info("Starting AWS EC2 instance: %s", instance_id)
            self.ec2.start_instances(InstanceIds=[instance_id])
            
            settings = get_settings()
            if settings.aws_agent_sg_id:
                logger.info("Enforcing Agent Security Group %s on %s", settings.aws_agent_sg_id, instance_id)
                self.ec2.modify_instance_attribute(
                    InstanceId=instance_id,
                    Groups=[settings.aws_agent_sg_id]
                )
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
            
    def terminate_instance(self, instance_id: str) -> None:
        """Terminate and permanently delete the cloud instance."""
        try:
            logger.info("Terminating AWS EC2 instance: %s", instance_id)
            self.ec2.terminate_instances(InstanceIds=[instance_id])
        except Exception as e:
            logger.error("Failed to terminate EC2 instance %s: %s", instance_id, e)
            raise

    def get_instance_ip(self, instance_id: str) -> str:
        """Get the public IP of the instance."""
        try:
            response = self.ec2.describe_instances(InstanceIds=[instance_id])
            instance = response['Reservations'][0]['Instances'][0]
            ip = instance.get('PublicIpAddress')
            if not ip:
                raise ValueError("Instance does not have a public IP address yet.")
            return ip
        except Exception as e:
            logger.error("Failed to get IP for EC2 instance %s: %s", instance_id, e)
            raise
