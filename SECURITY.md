# Security & Vulnerability Disclosure Program

**Last Updated:** August 26, 2026

At **Erex** and **BlockHost**, the security of our infrastructure, backend systems, and user data is our highest priority. We recognize the invaluable role that independent security researchers and the broader cybersecurity community play in maintaining a secure internet. 

This document outlines our Vulnerability Disclosure Program (VDP) and the strict rules of engagement for researching and reporting security vulnerabilities on our platforms.

---

## 1. Scope of the Program

This program covers the core infrastructure and proprietary applications operated directly by Erex and BlockHost. 

### 1.1 In-Scope Targets
The following assets are considered **In-Scope** for security research:
- The main Erex customer-facing website and application domains (e.g., `erex.com`, `app.erex.com`, `api.erex.com`).
- The BlockHost backend management APIs and administrative interfaces.
- The proprietary daemon software running on our virtualization nodes.
- Authentication flows, session management, and billing integration implementations (specifically our code, not Razorpay's code).

### 1.2 Out-of-Scope Targets
The following assets and scenarios are strictly **Out-of-Scope**. Attempting to test or exploit these is a violation of this policy and may result in legal action:
- **Third-Party Services:** Any infrastructure operated by our subprocessors (Amazon Web Services, Google Drive, Razorpay, Mojang APIs).
- **User Content:** The individual game servers, configurations, files, or applications hosted by our customers. You may not attack a customer's Minecraft server to test Erex.
- **Physical Security:** Any attempt to physically access AWS data centers or Erex offices.
- **Social Engineering:** Phishing, vishing, or any form of social engineering directed at Erex employees, contractors, or customers.
- **Denial of Service (DoS):** Any attack designed to exhaust resources, degrade performance, or take services offline (e.g., DDoS, volumetric attacks, slowloris).

## 2. Safe Harbor Provision

Erex considers security research conducted in good faith and in strict compliance with this document to be authorized. 

If you strictly adhere to the guidelines set forth in this policy:
1. We will not initiate or support criminal legal action against you related to your research.
2. We will not pursue civil litigation or seek damages against you for accidental, good-faith violations that occur during authorized research.
3. If legal action is initiated by a third party against you in relation to your research conducted under this policy, we will take reasonable steps to clarify that your actions were authorized by Erex.

**However, this Safe Harbor does not protect you if you violate the "Out-of-Scope" targets, intentionally steal data, or intentionally disrupt services.**

## 3. Rules of Engagement

To remain protected under the Safe Harbor provision, you must abide by the following Rules of Engagement at all times:

### 3.1 Non-Destructive Testing
You must not engage in any research that destroys, corrupts, or alters data. If a vulnerability provides unintended access to a database, you may execute a benign query (e.g., `SELECT version();` or `SELECT user();`) to prove the concept, but you **may not** dump tables, modify records, or access personally identifiable information (PII) of other users.

### 3.2 Privacy and Data Access
If you inadvertently encounter PII, financial data, or proprietary source code during your research, you must immediately halt your activity, close the connection, and report the vulnerability to us. You must not save, copy, transfer, or otherwise retain any accessed data.

### 3.3 Test Accounts Only
Whenever possible, you should conduct your research using test accounts that you have explicitly created and own. You must never attempt to exploit, access, or compromise the account of another legitimate Erex customer without their explicit, written consent.

### 3.4 Embargo and Public Disclosure
We ask for a coordinated disclosure process. You must not publicly disclose, discuss, or publish the vulnerability details (including on social media, blogs, or bug bounty platforms) until:
1. You have reported the vulnerability to us.
2. We have confirmed the vulnerability and deployed a patch to production.
3. We have given you explicit written permission to publish your findings.

We strive to resolve critical vulnerabilities within 14 days and non-critical vulnerabilities within 45 days. We will keep you updated throughout the remediation process.

## 4. Reporting a Vulnerability

If you believe you have discovered a security vulnerability that meets the criteria outlined in this policy, please report it immediately.

**Dedicated Security Contact:** seems.developer@gmail.com

To help us triage and resolve the issue quickly, your report must include:
1. **Description:** A detailed explanation of the vulnerability and its potential impact.
2. **Location:** The specific URL, endpoint, or IP address where the vulnerability exists.
3. **Steps to Reproduce:** Clear, step-by-step instructions on how to replicate the issue. (A video recording or screenshot is highly appreciated).
4. **Proof of Concept (PoC):** Benign exploit code, HTTP request/response logs, or scripts demonstrating the flaw.
5. **Impact Assessment:** Your professional opinion on what an attacker could achieve by exploiting this flaw.

## 5. Exclusions and Non-Qualifying Reports

We receive many reports for issues that do not pose a practical security threat. The following issues are generally excluded and will not be considered valid reports unless you can demonstrate a chained exploit resulting in a significant compromise:

- Missing HTTP security headers (e.g., Strict-Transport-Security, X-Frame-Options) without a demonstrable exploit like Clickjacking.
- SPF/DKIM/DMARC configuration issues.
- Disclosure of known public files or directories (e.g., `robots.txt`).
- Theoretical vulnerabilities without a working PoC.
- Use of a known-vulnerable library without proof that the vulnerable function is actually exposed and exploitable in our implementation.
- Issues requiring excessive user interaction or highly unlikely social engineering scenarios.

## 6. Recognition

Currently, Erex does not operate a paid Bug Bounty program (we do not offer financial rewards). However, for researchers who submit valid, impactful, and previously unknown vulnerabilities while strictly following this policy, we are happy to offer:
- Acknowledgment and gratitude.
- Free hosting credits or subscription upgrades on the Erex platform.
- Public recognition (if desired) once we establish a formal Hall of Fame.

Thank you for helping us keep the BlockHost infrastructure secure.
