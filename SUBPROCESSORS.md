# Subprocessors

Last Updated: September 22, 2026

To provide our Platform as a Service (PaaS) and Minecraft hosting capabilities, Blockhost engages the following third-party infrastructure and service providers ("Subprocessors"). 

By using Blockhost, you agree that your data (including Web App deployments, Databases, and Minecraft Worlds) may be stored, processed, or routed through the following entities:

## Core Infrastructure Providers

These providers host the physical servers, virtual machines, and network storage arrays where your containers and data reside.

### 1. Contabo GmbH
- **Purpose:** Core compute infrastructure. Provides the primary "Worker Nodes" that run the Blockhost Agent and host the Docker containers for the Shared Tier.
- **Location:** Germany (with data centers globally, depending on the region you select for your deployment).

### 2. Amazon Web Services, Inc. (AWS)
- **Purpose:** Premium compute, networking, and persistent storage.
- **Services Used:**
  - **Amazon EC2:** Provides the Game Proxy servers, Control Plane infrastructure, and dynamic Overflow Nodes when autoscaling is triggered.
  - **Amazon EFS (Elastic File System):** Provides the highly durable, network-attached storage volumes that persist your Database files and Web App uploads.
  - **Amazon S3:** Used for cold-storage backups of suspended Minecraft servers.
- **Location:** Global (e.g., US-East-1, EU-Central-1).

## Payment Processors

### 3. Stripe, Inc.
- **Purpose:** Securely processes all credit card payments, subscriptions, and billing transactions. Blockhost does not store your raw credit card numbers; they are tokenized and handled entirely by Stripe.
- **Location:** United States.

## Updates to this List

We reserve the right to add or change subprocessors as our infrastructure needs evolve. We will update this page to reflect any new infrastructure providers that may process your deployment data.
