# Acceptable Use Policy (AUP)

Welcome to Blockhost! Because we provide raw Platform as a Service (PaaS) capabilities, including Docker container hosting, databases, and custom web applications, we must enforce strict rules to ensure the safety, legality, and stability of our network.

By deploying any application, server, or database on Blockhost, you agree to abide by this Acceptable Use Policy.

## 1. Prohibited Activities

You are strictly prohibited from using Blockhost infrastructure to host, transmit, or facilitate any of the following:

- **Cryptocurrency Mining:** Hosting any script, Docker container, or application (e.g., Monero miners) designed to mine cryptocurrency is strictly forbidden. We monitor CPU usage patterns, and violators will be banned immediately without a refund.
- **Phishing and Malware:** Hosting deceptive websites designed to steal credentials, or distributing viruses, trojans, ransomware, or any other malicious software.
- **Network Abuse:** Using your deployment as a staging ground for Denial of Service (DDoS) attacks, network stress testing, port scanning, or unauthorized penetration testing.
- **Illegal Streaming & Copyright Infringement:** Hosting pirated content, illegal streaming platforms, or distributing copyrighted material without authorization. (See our DMCA Policy).
- **Spam:** Operating open mail relays, sending unsolicited bulk email, or hosting infrastructure designed to bypass spam filters.

## 2. Resource Quotas and Abuse

Blockhost utilizes a dynamic Orchestrator and Agent system to ensure fair resource distribution across Shared and Dedicated nodes.

- **Storage Limits:** Your deployments are bound by a strict persistent storage quota (default 5GB, unless upgraded). Attempting to bypass this quota by writing data outside of mounted volumes, filling up the OS root disk, or circumventing `du` checks will result in your deployment being forcibly suspended.
- **Memory (RAM) Abuse:** Deployments that aggressively exceed their purchased RAM limits will trigger the Linux Kernel's Out Of Memory (OOM) killer. Repeatedly causing OOM events that threaten node stability may result in suspension.
- **Fair Use Network I/O:** While we offer generous bandwidth, sustained saturation of our gigabit uplinks that degrades performance for other users on Shared Tiers is not permitted. 

## 3. Gaming Servers (Minecraft)

- You are welcome to host Minecraft servers and install custom plugins/mods.
- However, attempting to use Minecraft servers to bypass App Deployment restrictions (e.g., hiding web servers or proxies inside a Java plugin to evade billing limits) is prohibited.

## 4. Enforcement

Blockhost utilizes automated Agents that monitor generic metrics (CPU spikes, memory exhaustion, network flow, and disk usage). 

If a violation is detected:
1. We reserve the right to instantly suspend the offending Deployment, Database, or Server without prior warning.
2. In cases of severe abuse (e.g., Crypto-mining, Phishing, DDoS), your entire Blockhost account will be terminated immediately, and no refunds will be provided.
3. We will cooperate fully with law enforcement agencies investigating illegal activities originating from our network.

If you have questions about whether your specific workload is permitted, please contact our support team before deploying.
