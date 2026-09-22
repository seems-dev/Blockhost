# Blockhost Terms of Service

Last Updated: September 22, 2026

Welcome to Blockhost! By using our platform to host web applications, databases, or game servers, you agree to these Terms of Service.

## 1. Description of Service
Blockhost provides a Platform as a Service (PaaS) allowing users to deploy Docker containers, web applications, databases, and Minecraft servers onto our cloud infrastructure.

## 2. User Content & Liability
You retain all rights to the code, data, and content you deploy on Blockhost. However, you are solely responsible for:
- Ensuring your Web Apps, Databases, and Docker Images do not contain malicious code.
- Securing your applications against vulnerabilities. Blockhost is not responsible if your custom database or web app is hacked due to poor configuration or weak passwords.
- Ensuring you have the legal right to host the content you upload to our persistent EFS volumes.

Blockhost does not monitor the internal contents of your databases or applications, but we reserve the right to suspend any deployment that violates our Acceptable Use Policy.

## 3. Service Level Agreements (SLA) & Uptime
- **Shared Tier:** Deployments on our Shared Tier are provided on a "best-effort" basis. While we strive for high uptime, these nodes are shared among multiple users, and you may occasionally experience brief restarts during infrastructure rebalancing.
- **Dedicated Tier:** Deployments on the Dedicated Tier receive isolated resources. 
- **Scale-to-Zero & Auto-Shutdown:** Minecraft servers are subject to aggressive auto-sleeping to save resources. Web Apps and Databases are exempt from this and will run 24/7 as long as your account remains in good standing.

## 4. Backups and Data Loss
- While Blockhost provides automated world backups for Minecraft servers (via Amazon S3), **we do not guarantee the integrity of data within your Docker Web Apps or Databases**. 
- It is your responsibility to maintain off-site backups of your critical Postgres, MySQL, or application data stored on our EFS drives. Blockhost is not liable for data loss caused by user error, database corruption, or extreme infrastructure failure.

## 5. Billing and Fair Use
- You will be billed according to the RAM, CPU, and Storage limits of your selected tier. 
- You agree not to attempt to bypass automated billing quotas, including disk storage limits enforced by our background agents.

## 6. Account Termination
We reserve the right to terminate your account immediately, without refund, if you violate these Terms or our Acceptable Use Policy (such as attempting to host cryptocurrency miners or malware).
