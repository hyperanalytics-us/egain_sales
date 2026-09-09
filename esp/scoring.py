"""Sales Intent Score.

Weights follow the Scoring Guide in the reference analysis: explicit
conversion pages dominate, evaluation depth adds, and non-buyer behaviour
(careers, investor relations, support) subtracts.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

TIER_A_IMMEDIATE = "A - Immediate"
TIER_A_HIGH = "A - High"
TIER_B_WARM = "B - Warm"
TIER_C_NURTURE = "C - Nurture"
TIER_NONE = "Not eligible"

TIER_ORDER = [TIER_A_IMMEDIATE, TIER_A_HIGH, TIER_B_WARM, TIER_C_NURTURE]

TIER_ACTIONS = {
    TIER_A_IMMEDIATE: "Contact within 24h",
    TIER_A_HIGH: "Enrich to account, then outreach this week",
    TIER_B_WARM: "Enrich and add to active nurture",
    TIER_C_NURTURE: "Add to ABM / nurture pool; no direct outreach yet",
}

WEIGHTS_REFERENCE = [
    ("Demo page", "+45", "Strong explicit buying/evaluation intent"),
    ("Contact page", "+35", "Strong conversion intent"),
    ("Pricing", "+20", "Commercial evaluation signal"),
    ("Product/Solution depth", "up to +20", "Repeated product evaluation"),
    ("Industry/vertical depth", "up to +12", "Indicates use-case/segment fit"),
    ("Case study / proof", "up to +12", "Buyer is checking evidence/social proof"),
    ("Marketing email visit", "+10", "Ties traffic to outbound/nurture"),
    ("CRM UID present", "+10", "Can often map traffic back to a known CRM record"),
    ("Organic search", "+5", "Active discovery intent"),
    ("LinkedIn referral", "+5", "Business/social discovery intent"),
    ("Repeat sessions / days", "up to +16", "Sustained rather than one-off interest"),
    ("Career-heavy traffic", "-15 to -35", "Likely job seeker rather than buyer"),
    ("Investor/news-heavy traffic", "-10 to -25", "Often research/investor interest"),
    ("Support-heavy traffic", "-30", "Likely existing user/support behaviour"),
    ("Crawler risk", "-35 / excluded", "Reduces automated scanning noise"),
]

TIER_GUIDE = [
    (TIER_A_IMMEDIATE, "Score >= 75 with a Contact or Demo page view. Review first."),
    (TIER_A_HIGH, "Score >= 60. Strong evaluation or attribution signal."),
    (TIER_B_WARM, "Score 45-59. Worth enrichment and active nurture."),
    (TIER_C_NURTURE, "Score < 45 but at least one meaningful intent signal."),
]


def crawler_risk(stats: Dict) -> Tuple[str, str]:
    """Return (risk, reason).  Heuristic, and deliberately conservative."""
    reasons: List[str] = []
    if stats["is_bot_ua"]:
        reasons.append(f"declared bot user agent ({stats.get('bot_name') or 'generic'})")
    if stats["max_req_per_min"] >= 60:
        reasons.append(f"{stats['max_req_per_min']} requests in a single minute")
    if stats["unique_pages"] >= 250:
        reasons.append(f"{stats['unique_pages']} distinct pages scanned")
    span_min = max(1.0, (stats["last_ts"] - stats["first_ts"]) / 60.0)
    rate = stats["page_views"] / span_min
    if stats["page_views"] >= 200 and rate >= 5:
        reasons.append(f"sustained {rate:.0f} requests/minute")
    if reasons:
        return "High", "; ".join(reasons)

    soft: List[str] = []
    if stats["max_req_per_min"] >= 25:
        soft.append(f"{stats['max_req_per_min']} requests in a single minute")
    if stats["unique_pages"] >= 100:
        soft.append(f"{stats['unique_pages']} distinct pages scanned")
    if stats["page_views"] >= 100 and stats["unique_pages"] >= 60 and stats["sessions"] <= 2:
        soft.append("broad single-session sweep")
    if soft:
        return "Medium", "; ".join(soft)
    return "Low", ""


def score_ip(s: Dict) -> Tuple[int, str, str, bool, str]:
    """Return (score, components, tier, eligible, why)."""
    pts: List[Tuple[str, int]] = []

    if s["demo_views"] > 0:
        pts.append(("Demo", 45))
    if s["contact_views"] > 0:
        pts.append(("Contact", 35))
    if s["pricing_views"] > 0:
        pts.append(("Pricing", 20))
    if s["product_views"] > 0:
        pts.append(("Product eval", min(20, 4 + 2 * s["product_views"])))
    if s["industry_views"] > 0:
        pts.append(("Industry eval", min(12, 3 + 3 * s["industry_views"])))
    if s["proof_views"] > 0:
        pts.append(("Proof", min(12, 4 + 2 * s["proof_views"])))
    if s["marketing_email_views"] > 0:
        pts.append(("Email", 10))
    if s["uid"]:
        pts.append(("UID", 10))
    if s["organic_ref_views"] > 0:
        pts.append(("Organic", 5))
    if s["linkedin_ref_views"] > 0:
        pts.append(("LinkedIn", 5))

    repeat = min(16, 2 * max(0, s["sessions"] - 1) + 2 * max(0, s["active_days"] - 1))
    if repeat:
        pts.append(("Repeat engagement", repeat))

    if s["career_share"] >= 0.5:
        pts.append(("Career-heavy", -35))
    elif s["career_share"] >= 0.25:
        pts.append(("Career-leaning", -15))

    if s["investor_share"] >= 0.5:
        pts.append(("Investor-heavy", -25))
    elif s["investor_share"] >= 0.25:
        pts.append(("Investor-leaning", -10))

    if s["support_share"] >= 0.5:
        pts.append(("Support-heavy", -30))

    if s["crawler_risk"] == "High":
        pts.append(("Crawler risk", -35))
    elif s["crawler_risk"] == "Medium":
        pts.append(("Crawler risk", -10))

    # Clamp the positive signals first so the penalties actually bite: an IP that
    # maxes out every intent signal should still fall when it looks like a crawler.
    positives = min(100, sum(v for _, v in pts if v > 0))
    penalties = sum(v for _, v in pts if v < 0)
    score = max(0, min(100, positives + penalties))
    components = "; ".join(f"{k} {v:+d}" for k, v in pts) or "no scoring signals"

    has_intent = any(
        s[k] > 0
        for k in ("contact_views", "demo_views", "pricing_views", "product_views", "industry_views", "proof_views")
    ) or bool(s["uid"]) or s["marketing_email_views"] > 0

    eligible = has_intent and not s["is_bot_ua"]

    if not eligible:
        tier = TIER_NONE
    elif score >= 75 and (s["contact_views"] > 0 or s["demo_views"] > 0):
        tier = TIER_A_IMMEDIATE
    elif score >= 60:
        tier = TIER_A_HIGH
    elif score >= 45:
        tier = TIER_B_WARM
    else:
        tier = TIER_C_NURTURE

    why: List[str] = []
    if s["demo_views"]:
        why.append(f"{s['demo_views']} demo/trial page view(s)")
    if s["contact_views"]:
        why.append(f"{s['contact_views']} contact page view(s)")
    if s["pricing_views"]:
        why.append("viewed pricing")
    if s["products"]:
        why.append("interest: " + s["products"])
    if s["industries"]:
        why.append("vertical: " + s["industries"])
    if s["marketing_email_views"]:
        why.append("marketing-email click")
    if s["uid"]:
        why.append("CRM UID available")
    if s["sessions"] > 1:
        why.append(f"{s['sessions']} sessions over {s['active_days']} day(s)")
    if s["crawler_risk"] != "Low":
        why.append(f"{s['crawler_risk'].lower()} crawler risk")

    return score, components, tier, eligible, "; ".join(why)


def next_action(s: Dict, tier: str) -> str:
    if tier == TIER_NONE:
        return "No action - below the intent threshold"
    if s.get("uid"):
        return "Match CRM UID to contact, then review and reach out"
    if tier == TIER_A_IMMEDIATE:
        return "Enrich IP to account; contact within 24h"
    if tier == TIER_A_HIGH:
        return "Enrich IP to account; outreach this week"
    if tier == TIER_B_WARM:
        return "Enrich and add to active nurture"
    return "Add to ABM / nurture pool"
