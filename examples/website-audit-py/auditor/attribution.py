"""Third-party attribution — who is responsible for a finding.

Findings often point at ad/consent/tracker hosts the site owner can't fix
in their own markup. `attribute()` classifies a finding's evidence URLs as
first-party (the site's own host or subdomains) or a known/unknown vendor,
so reports can say "this one is Google's tag, not your code".
"""

import urllib.parse

# host suffix -> (friendly vendor name, kind)
VENDORS = {
    "googlesyndication.com": ("Google Ads", "ads"),
    "doubleclick.net": ("Google Ads", "ads"),
    "googleadservices.com": ("Google Ads", "ads"),
    "amazon-adsystem.com": ("Amazon Ads", "ads"),
    "criteo.com": ("Criteo", "ads"),
    "adnxs.com": ("Xandr", "ads"),
    "taboola.com": ("Taboola", "ads"),
    "outbrain.com": ("Outbrain", "ads"),
    "lngtd.com": ("Freestar", "ads"),
    "google-analytics.com": ("Google Analytics", "analytics"),
    "googletagmanager.com": ("Google Tag Manager", "analytics"),
    "hotjar.com": ("Hotjar", "analytics"),
    "clarity.ms": ("Microsoft Clarity", "analytics"),
    "cloudflareinsights.com": ("Cloudflare Insights", "analytics"),
    "hubspot.com": ("HubSpot", "analytics"),
    "hs-scripts.com": ("HubSpot", "analytics"),
    "privacy-mgmt.com": ("Sourcepoint", "consent"),
    "youtube.com": ("YouTube", "video"),
    "ytimg.com": ("YouTube", "video"),
    "vimeo.com": ("Vimeo", "video"),
    "vimeocdn.com": ("Vimeo", "video"),
    "facebook.net": ("Meta Pixel", "social"),
    "facebook.com": ("Meta Pixel", "social"),
    "intercom.io": ("Intercom", "chat"),
    "intercom.com": ("Intercom", "chat"),
    "perfdrive.com": ("Radware Bot Manager", "bot-protection"),
    "fonts.googleapis.com": ("Google Fonts", "cdn-fonts"),
    "fonts.gstatic.com": ("Google Fonts", "cdn-fonts"),
}

# longest suffix first so e.g. fonts.gstatic.com wins over gstatic.com
_VENDOR_SUFFIXES = sorted(VENDORS, key=len, reverse=True)


def _bare(host):
    h = (host or "").lower().split(":")[0].strip(".")
    return h[4:] if h.startswith("www.") else h


def _host_of(url):
    u = str(url or "")
    return urllib.parse.urlparse(
        u if "://" in u else "https://" + u).netloc


def is_first_party(url, site_host):
    """Same host or a subdomain of the site's host (www. ignored)."""
    host, site = _bare(_host_of(url)), _bare(site_host)
    return bool(site) and (host == site or host.endswith("." + site))


def _vendor(host):
    h = _bare(host)
    for suffix in _VENDOR_SUFFIXES:
        if h == suffix or h.endswith("." + suffix):
            name, kind = VENDORS[suffix]
            return {"name": name, "kind": kind, "host": host}
    return {"name": host, "kind": "other", "host": host}


def attribute(finding, site_host):
    """Classify a finding by who owns its evidence URLs.

    Page-level findings (urls = the audited page) are first-party by
    definition. Returns {"owner": site|third_party|mixed, counts, vendors}."""
    urls = (finding.get("evidence") or {}).get("urls") or []
    site = third = 0
    vendors = {}
    for u in urls:
        host = _host_of(u)
        if not host or is_first_party(u, site_host):
            site += 1
        else:
            third += 1
            v = _vendor(host)
            vendors.setdefault(v["name"], v)
    owner = "site" if not third else ("mixed" if site else "third_party")
    return {"owner": owner, "site_count": site,
            "third_party_count": third,
            "vendors": list(vendors.values())[:5]}
