"""Public legal pages for app store and website compliance."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/legal", tags=["legal"])

_PAGES: dict[str, dict[str, str]] = {
    "terms": {
        "title": "Terms of Service",
        "summary": "BlockHost Minecraft Bedrock server hosting",
        "body": """
<h1>Terms of Service</h1>
<p><strong>Last updated:</strong> July 2026</p>
<h2>1. Service</h2>
<p>BlockHost provides managed Minecraft Bedrock server hosting. By creating an account or purchasing a subscription, you agree to these terms.</p>
<h2>2. Accounts</h2>
<p>You are responsible for keeping your credentials secure. You must provide accurate contact information.</p>
<h2>3. Acceptable use</h2>
<ul>
<li>No illegal content, harassment, or copyright infringement on hosted worlds.</li>
<li>No attempts to attack, reverse-engineer, or overload the platform.</li>
<li>Resource limits (RAM, players, storage) are enforced per subscription plan.</li>
</ul>
<h2>4. Subscriptions &amp; billing</h2>
<p>Plans renew according to the billing period selected at purchase. Failed payments may suspend servers after the grace period.</p>
<h2>5. Data &amp; worlds</h2>
<p>You retain ownership of your world data. After subscription lapse, worlds may be retained for a limited period then deleted.</p>
<h2>6. Limitation of liability</h2>
<p>BlockHost is provided &quot;as is&quot;. We are not liable for downtime, data loss beyond our backup retention, or third-party game client issues.</p>
<h2>7. Contact</h2>
<p>Support: support@blockhost.example</p>
""",
    },
    "privacy": {
        "title": "Privacy Policy",
        "summary": "How BlockHost handles your data",
        "body": """
<h1>Privacy Policy</h1>
<p><strong>Last updated:</strong> July 2026</p>
<h2>Data we collect</h2>
<ul>
<li>Account: email, nickname, authentication credentials (hashed).</li>
<li>Billing: payment references via Razorpay (we do not store card numbers).</li>
<li>Server: world files, configuration, logs, player connection metadata.</li>
<li>Technical: IP address, device type, API usage for security and rate limiting.</li>
</ul>
<h2>How we use data</h2>
<p>To operate servers, process payments, provide support, prevent abuse, and improve reliability.</p>
<h2>Sharing</h2>
<p>We share data only with payment processors (Razorpay), infrastructure providers, and when required by law.</p>
<h2>Retention</h2>
<p>Account data is kept while your account is active. World data retention after cancellation is described in our Refund Policy.</p>
<h2>Your rights</h2>
<p>Contact support@blockhost.example to request account deletion or data export.</p>
""",
    },
    "refunds": {
        "title": "Refund Policy",
        "summary": "Subscription refunds and world retention",
        "body": """
<h1>Refund Policy</h1>
<p><strong>Last updated:</strong> July 2026</p>
<h2>Subscriptions</h2>
<p>Monthly plans are generally non-refundable once the server billing period has started, except where required by local consumer law or at our discretion for billing errors.</p>
<h2>Grace period</h2>
<p>When a subscription expires, servers enter a grace period (default 3 days) during which you can renew without losing your world.</p>
<h2>After suspension</h2>
<p>Suspended servers stop running but world files may be retained for up to 30 days. After that, data may be permanently deleted.</p>
<h2>Chargebacks</h2>
<p>Unauthorized chargebacks may result in immediate account suspension.</p>
<h2>Contact</h2>
<p>Refund requests: billing@blockhost.example</p>
""",
    },
}


@router.get("")
def list_legal_pages() -> list[dict[str, str]]:
    return [
        {"slug": slug, "title": page["title"], "url": f"/legal/{slug}"}
        for slug, page in _PAGES.items()
    ]


@router.get("/{slug}")
def get_legal_page(slug: str) -> dict[str, str]:
    page = _PAGES.get(slug)
    if not page:
        raise HTTPException(status_code=404, detail="Legal page not found")
    return {"slug": slug, "title": page["title"], "summary": page["summary"], "html": page["body"].strip()}


@router.get("/{slug}/html", response_class=HTMLResponse)
def get_legal_page_html(slug: str) -> HTMLResponse:
    page = _PAGES.get(slug)
    if not page:
        raise HTTPException(status_code=404, detail="Legal page not found")
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{page['title']} — BlockHost</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #1a1a1a; }}
h1 {{ font-size: 1.75rem; }} h2 {{ font-size: 1.1rem; margin-top: 1.5rem; }}
a {{ color: #0891b2; }}
</style>
</head><body>{page['body']}</body></html>"""
    return HTMLResponse(content=html)
