# Erex Subprocessors and Data Transfer Policy

**Last Updated:** August 26, 2026

To provide our raw infrastructure hosting services securely, reliably, and at scale, **Sims Kushawaha**, operating as **Erex** and **BlockHost** ("we", "us", "our"), engages third-party service providers and data processors, collectively referred to as "Subprocessors."

This document outlines the specific entities we authorize to process customer personal data and Server Content on our behalf. By agreeing to our Terms of Service and Privacy Policy, you explicitly consent to the processing of your data by these Subprocessors.

---

## 1. What is a Subprocessor?

A Subprocessor is a third-party data processor engaged by Erex who has or potentially will have access to or process Customer Data (which may include personal data, billing information, or Server Content) in order to facilitate the delivery of the Erex services.

Erex remains responsible for the acts and omissions of its Subprocessors to the extent required by applicable data protection laws, including the Indian Digital Personal Data Protection (DPDP) Act and equivalent global frameworks.

## 2. Infrastructure & Cloud Subprocessors

These Subprocessors provide the foundational hardware, network, and virtualization layers that run the BlockHost daemon and your individual provisioned servers. Your data inherently resides on their physical infrastructure.

### Amazon Web Services, Inc. (AWS)
- **Role:** Primary Cloud Infrastructure Provider (IaaS). AWS hosts the hypervisors, databases, and network backbones for Erex and BlockHost.
- **Data Processed:** All Server Content (world files, configurations, databases), IP addresses, network traffic logs, and encrypted customer account data.
- **Location of Processing:** Dependent on the specific region you select during server deployment (e.g., ap-south-1 for Mumbai, India, or us-east-1 for Virginia, USA). AWS utilizes a global network of data centers.
- **Legal Safeguards:** AWS processes data in accordance with their Data Processing Addendum (DPA) and strict international security standards (SOC 2, ISO 27001). Erex retains administrative control over the virtual machines; AWS does not proactively access Erex Customer Data except as required by law.

## 3. Payment Processing Subprocessors

These Subprocessors are strictly utilized to handle financial transactions, manage recurring subscriptions, and prevent credit card fraud. Erex **never** stores your full credit card PAN or CVV on our servers; it is tokenized and transmitted directly to the payment processor.

### Razorpay Software Private Limited
- **Role:** Primary Payment Gateway and Merchant of Record.
- **Data Processed:** Customer Name, Billing Email, Payment Token, Transaction History, IP Address (for fraud detection), and Subscription status.
- **Location of Processing:** Primarily India, subject to RBI data localization mandates.
- **Legal Safeguards:** Razorpay is strictly PCI-DSS Level 1 compliant. They process financial data under their own independent Privacy Policy and Terms of Use.

## 4. User Integration & Optional Subprocessors

These Subprocessors are only engaged if you, the customer, actively choose to utilize specific features or integrations within the Erex dashboard.

### Google LLC (Google Drive API)
- **Role:** Offsite Backup and Storage Provider (Opt-In).
- **Data Processed:** If you authorize the Google Drive integration, Erex will transmit automated archives of your Server Content (.zip/.tar files containing worlds, plugins, and databases) directly to your personal Google Drive account.
- **Location of Processing:** Global (Google's data centers).
- **Legal Safeguards:** You authenticate directly via OAuth 2.0. Erex only requests the scopes necessary to upload files to a specific directory. Once the data reaches Google Drive, it is governed exclusively by your personal agreement with Google and Google's data retention policies.

### Mojang AB / Microsoft Corporation (Minecraft APIs)
- **Role:** Game Software Provider.
- **Data Processed:** When you provision a Minecraft server, Erex utilizes Mojang's official APIs to download the necessary `.jar` binaries and validate player UUIDs. Server IP addresses and player connection data may interact with Mojang's authentication servers.
- **Location of Processing:** Global.
- **Legal Safeguards:** This data transfer is fundamental to the operation of the Minecraft game. It is governed by the Minecraft EULA.

## 5. Right to Object and Notice of Changes

### 5.1 Essential Providers
The Subprocessors listed in Sections 2 and 3 (AWS and Razorpay) are strictly essential to the operation of Erex. It is technically impossible to provide the Service without them. Therefore, you do not have the right to object to the use of these specific Subprocessors while maintaining an active account. If you disagree with our use of AWS or Razorpay, you must immediately cancel your subscription and terminate your account.

### 5.2 Notification of New Subprocessors
As our infrastructure scales, we may need to engage additional Subprocessors (e.g., adding Cloudflare for DDoS protection, or Datadog for system logging). 

We will provide notification of any new Subprocessors by updating this document at least **30 days** before authorizing the new Subprocessor to access Customer Data. We may also notify active customers via email or an in-app dashboard announcement.

Your continued use of the Erex services after the 30-day notice period constitutes your binding acceptance of the new Subprocessor.

## 6. International Data Transfers

Given the global nature of cloud computing, your data may be transferred to, and processed in, countries other than the country in which you reside. By using Erex, you consent to this transfer.

When we transfer personal data originating from jurisdictions with strict data localization laws, we rely on established legal mechanisms, including:
- Utilizing data centers located within the mandated jurisdiction (e.g., using AWS Mumbai for Indian customers where required).
- Ensuring our Subprocessors execute Standard Contractual Clauses (SCCs) or adhere to equivalent adequacy frameworks.

If you have specific compliance requirements regarding data residency, you must contact us prior to provisioning a server.

**Contact for Data Processing Inquiries:** seems.developer@gmail.com
