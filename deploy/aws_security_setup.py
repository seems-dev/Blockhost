#!/usr/bin/env python3
"""
BlockHost AWS Security Hardening Script

This script performs the following network security lockdowns for the Agent EC2s:
1. Network Isolation: Modifies the Agent's Inbound Security Group to ONLY allow traffic from the Proxy VM, Control Plane, and EFS.
2. Egress Firewall: Modifies the Agent's Outbound Security Group to restrict outbound traffic to necessary ports and services, preventing crypto-miners and DDoS bots.
3. Public IP Removal: Attempts to disassociate any Elastic IPs attached to Agent instances. (Note: Auto-assigned public IPs cannot be removed; those instances must be recreated in a private subnet).

Requirements:
- pip install boto3
- AWS Credentials configured (e.g., via aws configure or environment variables)
- IAM Permissions: ec2:DescribeSecurityGroups, ec2:AuthorizeSecurityGroupIngress, ec2:RevokeSecurityGroupIngress, 
                   ec2:AuthorizeSecurityGroupEgress, ec2:RevokeSecurityGroupEgress, ec2:DescribeInstances,
                   ec2:DescribeAddresses, ec2:DisassociateAddress
"""

import argparse
import logging
import sys

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    print("Error: boto3 is not installed. Please run `pip install boto3`")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def revoke_all_rules(ec2_client, group_id, direction="ingress"):
    """Revoke all existing rules in the specified security group for a given direction."""
    try:
        response = ec2_client.describe_security_groups(GroupIds=[group_id])
        sg = response['SecurityGroups'][0]
        
        if direction == "ingress":
            permissions = sg.get('IpPermissions', [])
            if permissions:
                logger.info(f"Revoking {len(permissions)} existing inbound rules from {group_id}...")
                ec2_client.revoke_security_group_ingress(GroupId=group_id, IpPermissions=permissions)
        elif direction == "egress":
            permissions = sg.get('IpPermissionsEgress', [])
            if permissions:
                logger.info(f"Revoking {len(permissions)} existing outbound rules from {group_id}...")
                ec2_client.revoke_security_group_egress(GroupId=group_id, IpPermissions=permissions)
                
    except ClientError as e:
        logger.error(f"Failed to revoke rules for {group_id}: {e}")
        sys.exit(1)


def configure_inbound_rules(ec2_client, agent_sg, control_sg, proxy_sg, efs_sg):
    """Set up the strict inbound rules for the Agent."""
    logger.info("Configuring Inbound Rules...")
    revoke_all_rules(ec2_client, agent_sg, direction="ingress")
    
    inbound_rules = [
        # Control Plane -> Agent (TCP 9000)
        {
            'IpProtocol': 'tcp',
            'FromPort': 9000,
            'ToPort': 9000,
            'UserIdGroupPairs': [{'GroupId': control_sg, 'Description': 'Control Plane API'}]
        },
        # Proxy VM -> Agent (TCP 25565-25665) Java
        {
            'IpProtocol': 'tcp',
            'FromPort': 25565,
            'ToPort': 25665,
            'UserIdGroupPairs': [{'GroupId': proxy_sg, 'Description': 'Proxy VM Java Traffic'}]
        },
        # Proxy VM -> Agent (UDP 19132-19232) Bedrock
        {
            'IpProtocol': 'udp',
            'FromPort': 19132,
            'ToPort': 19232,
            'UserIdGroupPairs': [{'GroupId': proxy_sg, 'Description': 'Proxy VM Bedrock Traffic'}]
        },
        # EFS -> Agent (TCP 2049) NFS (Usually Agent -> EFS, but sometimes EFS needs inbound callbacks, though mostly EFS SG needs inbound from Agent)
        # Adding it just in case, but typically EFS SG allows inbound from Agent SG.
        {
            'IpProtocol': 'tcp',
            'FromPort': 2049,
            'ToPort': 2049,
            'UserIdGroupPairs': [{'GroupId': efs_sg, 'Description': 'EFS NFS'}]
        }
    ]
    
    try:
        ec2_client.authorize_security_group_ingress(GroupId=agent_sg, IpPermissions=inbound_rules)
        logger.info("Successfully configured inbound rules.")
    except ClientError as e:
        logger.error(f"Failed to authorize inbound rules: {e}")


def configure_outbound_rules(ec2_client, agent_sg, control_sg, efs_sg):
    """Set up the strict outbound rules for the Agent."""
    logger.info("Configuring Outbound Rules...")
    revoke_all_rules(ec2_client, agent_sg, direction="egress")
    
    outbound_rules = [
        # HTTP/HTTPS to Internet (for downloading mods, jars, etc.)
        {
            'IpProtocol': 'tcp',
            'FromPort': 443,
            'ToPort': 443,
            'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'HTTPS downloads'}]
        },
        {
            'IpProtocol': 'tcp',
            'FromPort': 80,
            'ToPort': 80,
            'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'HTTP downloads'}]
        },
        # DNS to Internet
        {
            'IpProtocol': 'udp',
            'FromPort': 53,
            'ToPort': 53,
            'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'DNS UDP'}]
        },
        {
            'IpProtocol': 'tcp',
            'FromPort': 53,
            'ToPort': 53,
            'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'DNS TCP'}]
        },
        # Agent -> Control Plane (TCP 9000 for heartbeat, etc if needed. Usually Agent connects to CP via 443 or similar, but adding as requested)
        {
            'IpProtocol': 'tcp',
            'FromPort': 9000,
            'ToPort': 9000,
            'UserIdGroupPairs': [{'GroupId': control_sg, 'Description': 'Control Plane'}]
        },
        # Agent -> EFS (TCP 2049 NFS)
        {
            'IpProtocol': 'tcp',
            'FromPort': 2049,
            'ToPort': 2049,
            'UserIdGroupPairs': [{'GroupId': efs_sg, 'Description': 'EFS NFS'}]
        }
    ]
    
    try:
        ec2_client.authorize_security_group_egress(GroupId=agent_sg, IpPermissions=outbound_rules)
        logger.info("Successfully configured outbound rules.")
    except ClientError as e:
        logger.error(f"Failed to authorize outbound rules: {e}")


def disassociate_public_ips(ec2_client, agent_sg):
    """Find all instances using the Agent SG and attempt to disassociate public IPs."""
    logger.info("Checking Agent instances for Public IPs...")
    try:
        instances_resp = ec2_client.describe_instances(
            Filters=[
                {'Name': 'instance.group-id', 'Values': [agent_sg]},
                {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
            ]
        )
        
        instance_ids = []
        for r in instances_resp.get('Reservations', []):
            for i in r.get('Instances', []):
                instance_ids.append(i['InstanceId'])
                
        if not instance_ids:
            logger.info("No running Agent instances found.")
            return

        addresses_resp = ec2_client.describe_addresses()
        eip_map = {addr.get('InstanceId'): addr.get('AssociationId') for addr in addresses_resp.get('Addresses', []) if 'InstanceId' in addr}
        
        for r in instances_resp.get('Reservations', []):
            for i in r.get('Instances', []):
                inst_id = i['InstanceId']
                public_ip = i.get('PublicIpAddress')
                
                if public_ip:
                    if inst_id in eip_map:
                        assoc_id = eip_map[inst_id]
                        logger.info(f"Instance {inst_id} has an Elastic IP ({public_ip}). Disassociating...")
                        ec2_client.disassociate_address(AssociationId=assoc_id)
                        logger.info(f"Successfully disassociated EIP from {inst_id}.")
                    else:
                        logger.warning(f"Instance {inst_id} has an AUTO-ASSIGNED Public IP ({public_ip}).")
                        logger.warning("AWS does not allow removing auto-assigned public IPs from running instances.")
                        logger.warning(f"ACTION REQUIRED: You must recreate instance {inst_id} in a private subnet (with a NAT Gateway) for full network isolation.")
                else:
                    logger.info(f"Instance {inst_id} is correctly isolated (No Public IP).")

    except ClientError as e:
        logger.error(f"Failed to check/modify instances: {e}")


def main():
    parser = argparse.ArgumentParser(description="Lockdown AWS Security Groups for BlockHost Agents")
    parser.add_argument("--agent-sg", required=True, help="Security Group ID for the Agent Nodes")
    parser.add_argument("--control-sg", required=True, help="Security Group ID for the Control Plane")
    parser.add_argument("--proxy-sg", required=True, help="Security Group ID for the Proxy VM")
    parser.add_argument("--efs-sg", required=True, help="Security Group ID for the EFS Mount Target")
    parser.add_argument("--region", default="us-east-1", help="AWS Region (default: us-east-1)")
    
    args = parser.parse_args()
    
    ec2_client = boto3.client('ec2', region_name=args.region)
    
    configure_inbound_rules(ec2_client, args.agent_sg, args.control_sg, args.proxy_sg, args.efs_sg)
    configure_outbound_rules(ec2_client, args.agent_sg, args.control_sg, args.efs_sg)
    disassociate_public_ips(ec2_client, args.agent_sg)
    
    logger.info("Security hardening complete!")


if __name__ == "__main__":
    main()
