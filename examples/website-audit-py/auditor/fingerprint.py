"""Technology fingerprinting — BuiltWith-style detection over the data we
already collect (HTML, response headers, resource URLs, cookies).

Each signature = (name, category, matcher). Matchers search a haystack of
lowercased page HTML + resource URLs + header keys/values. This is the same
technique Wappalyzer/BuiltWith use — theirs just has ~100k signatures and
years of history behind it.
"""

# (name, category, [haystack needles]) — first match wins per signature
TECH_SIGNATURES = [
    # --- analytics & tag managers ---
    ("Google Analytics", "analytics", ["google-analytics.com", "gtag/js", "ga.js", "analytics.js"]),
    ("Google Tag Manager", "tag_manager", ["googletagmanager.com"]),
    ("Facebook Pixel", "analytics", ["connect.facebook.net", "fbevents.js"]),
    ("Hotjar", "analytics", ["hotjar.com", "static.hotjar.com"]),
    ("Mixpanel", "analytics", ["cdn.mxpnl.com", "mixpanel"]),
    ("Segment", "analytics", ["cdn.segment.com", "analytics.min.js"]),
    ("Plausible", "analytics", ["plausible.io/js/"]),
    ("PostHog", "analytics", ["posthog", "app.posthog.com"]),
    ("Microsoft Clarity", "analytics", ["clarity.ms"]),
    ("Adobe Analytics", "analytics", ["omtrdc.net", "adobedtm.com"]),
    # --- CDN / infra ---
    ("Cloudflare", "cdn", ["cloudflare", "cf-ray", "__cf_bm"]),
    ("CloudFront", "cdn", ["cloudfront.net"]),
    ("Fastly", "cdn", ["fastly", "x-served-by"]),
    ("Akamai", "cdn", ["akamai", "akamaized.net"]),
    ("jsDelivr", "cdn", ["cdn.jsdelivr.net"]),
    ("unpkg", "cdn", ["unpkg.com"]),
    # --- frameworks ---
    ("jQuery", "js_lib", ["jquery"]),
    ("React", "framework", ["react", "data-reactroot", "__react"]),
    ("Next.js", "framework", ["__next", "next/static", "_next/"]),
    ("Vue.js", "framework", ["vue.js", "vue.min.js", "__vue__", "data-v-"]),
    ("Angular", "framework", ["ng-app", "angular", "ng-version"]),
    ("Svelte", "framework", ["svelte"]),
    ("Bootstrap", "css_framework", ["bootstrap"]),
    ("Tailwind CSS", "css_framework", ["tailwind"]),
    # --- platforms / builders ---
    ("WordPress", "platform", ["wp-content", "wp-includes", "wp-json"]),
    ("Wix", "platform", ["wix.com", "x-wix-"]),
    ("Framer", "platform", ["framerusercontent", "framer.com"]),
    ("Shopify", "platform", ["cdn.shopify.com", "myshopify"]),
    ("Squarespace", "platform", ["squarespace"]),
    ("Webflow", "platform", ["webflow.com", "assets.website-files.com"]),
    ("Ghost", "platform", ["ghost/api", "/ghost/"]),
    ("Drupal", "platform", ["drupal", "sites/default/files"]),
    ("Joomla", "platform", ["joomla", "media/jui/"]),
    # --- payments / commerce ---
    ("Stripe", "payments", ["js.stripe.com", "stripe.com"]),
    ("PayPal", "payments", ["paypal.com", "paypalobjects.com"]),
    ("Klarna", "payments", ["klarna"]),
    # --- fonts / media ---
    ("Google Fonts", "fonts", ["fonts.googleapis.com", "fonts.gstatic.com"]),
    ("Adobe Fonts", "fonts", ["use.typekit.net", "typekit"]),
    ("Font Awesome", "fonts", ["fontawesome", "font-awesome"]),
    ("YouTube embeds", "media", ["youtube.com/embed", "youtube-nocookie.com"]),
    ("Vimeo embeds", "media", ["player.vimeo.com"]),
    # --- support / marketing widgets ---
    ("Intercom", "support_widget", ["intercom", "widget.intercom.io"]),
    ("Zendesk", "support_widget", ["zendesk", "zdassets.com"]),
    ("Drift", "support_widget", ["drift.com", "driftt.com"]),
    ("HubSpot", "marketing", ["hubspot", "hs-scripts.com", "hsforms"]),
    ("Mailchimp", "marketing", ["mailchimp", "list-manage.com"]),
    ("reCAPTCHA", "captcha", ["google.com/recaptcha", "gstatic.com/recaptcha"]),
    ("hCaptcha", "captcha", ["hcaptcha.com"]),
    ("Cloudflare Turnstile", "captcha", ["challenges.cloudflare.com/turnstile"]),
]


def detect_technologies(page):
    """Return sorted list of {name, category} detected on a page record."""
    haystacks = [
        (page.get("html") or "").lower(),
        " ".join(r.get("url", "") for r in page.get("responses", [])).lower(),
        " ".join(f"{k}:{v}" for k, v in (page.get("doc_headers") or {}).items()).lower(),
        " ".join(s.get("src", "") for s in page.get("dom", {}).get("headScripts", [])).lower(),
    ]
    haystack = " ".join(haystacks)

    found = []
    for name, category, needles in TECH_SIGNATURES:
        if any(n in haystack for n in needles):
            found.append({"name": name, "category": category})
    # dedupe (signatures can overlap)
    seen, out = set(), []
    for t in found:
        if t["name"] not in seen:
            seen.add(t["name"])
            out.append(t)
    return sorted(out, key=lambda t: (t["category"], t["name"]))
