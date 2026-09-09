"""URL / referrer / user-agent classification for egain.com weblogs.

The rules here were derived by mining the 530k-request reference log: every
pattern below corresponds to page families that actually appear in the file.
Order matters -- the first matching rule wins.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------
# Page categories.  A page gets exactly one primary category.
# --------------------------------------------------------------------------
CAT_DEMO = "demo"
CAT_CONTACT = "contact"
CAT_PRICING = "pricing"
CAT_PRODUCT = "product"
CAT_INDUSTRY = "industry"
CAT_PROOF = "proof"
CAT_BLOG = "blog"
CAT_CAREER = "career"
CAT_INVESTOR = "investor"
CAT_SUPPORT = "support"
CAT_PARTNER = "partner"
CAT_ADMIN = "admin"          # opt-outs, privacy, feeds, sitemaps -- noise
CAT_OTHER = "other"

INTENT_CATEGORIES = {CAT_DEMO, CAT_CONTACT, CAT_PRICING, CAT_PRODUCT, CAT_INDUSTRY, CAT_PROOF}

CATEGORY_LABELS = {
    CAT_DEMO: "Demo / Trial",
    CAT_CONTACT: "Contact",
    CAT_PRICING: "Pricing",
    CAT_PRODUCT: "Product / Solution",
    CAT_INDUSTRY: "Industry / Vertical",
    CAT_PROOF: "Proof / Content",
    CAT_BLOG: "Blog",
    CAT_CAREER: "Careers",
    CAT_INVESTOR: "Investor / News",
    CAT_SUPPORT: "Support / Developer",
    CAT_PARTNER: "Partner",
    CAT_ADMIN: "Admin / Compliance",
    CAT_OTHER: "Other",
}

# (compiled pattern, category)
_CATEGORY_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"/(forms-request-demo|request-a-demo|request-demo|book-a-demo|free-trial|risk-free-trial|start-trial|get-started)"), CAT_DEMO),
    (re.compile(r"/(contact-us|contact_us|contactus|talk-to-(us|sales)|company/contact)"), CAT_CONTACT),
    (re.compile(r"/(pricing|price|plans-and-pricing|cost-of)"), CAT_PRICING),
    (re.compile(r"/company/careers"), CAT_CAREER),
    (re.compile(r"/(company/investors|company/news|company/annual-reports|company/board-of-directors|company/executive-team|press-release|press_release|press-clipping|investor)"), CAT_INVESTOR),
    (re.compile(r"/(egain-support|support|dev-central|econet|community|documentation|/docs/|knowledge-base-login|customer-portal)"), CAT_SUPPORT),
    (re.compile(r"/(partners|partner-deal-registration|partner-locator|become-a-partner)"), CAT_PARTNER),
    (re.compile(r"/(opt-out-request|copyright|privacy-polic|terms-of|cookie|sitemap|/feed/|wp-|xmlrpc|robots\.txt|unsubscribe|form-subscription)"), CAT_ADMIN),
]

_PROOF_PAT = re.compile(
    r"(/success/|case-stud|success-stor|customer-success|testimonial|/awards|gartner|forrester|analyst-report"
    r"|white[-_]paper|research-report|webinar|podcast|ebook|/resources/|market-guide|magic-quadrant|roi-)"
)
_BLOG_PAT = re.compile(r"^/(de/)?blog(/|$)")

# --------------------------------------------------------------------------
# Products.  A page can only belong to one product line.
# --------------------------------------------------------------------------
PRODUCT_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(1_ai_knowledge_platform|ai-knowledge-platform|best-ai-knowledge-platform)"), "AI Knowledge Platform"),
    (re.compile(r"(ai-knowledge-hub|knowledge-hub|/products/knowledge-hub)"), "AI Knowledge Hub"),
    (re.compile(r"(ai-coach)"), "AI Coach"),
    (re.compile(r"(generative-ai|genai|conversational-ai)"), "Generative AI"),
    (re.compile(r"(chatbot|virtual-assistant|/products/social)"), "Virtual Assistant / Chatbot"),
    (re.compile(r"(/products/analytics|analytics-for-|/analytics/)"), "Analytics"),
    (re.compile(r"(self-service|customer-self-service|digital-self-service|smart-ivr|click-to-call|cobrowse|/products/notify|call-tracking)"), "Customer Self-Service"),
    (re.compile(r"(live-chat|email-management-software|contact-center|conversation-hub|service-cloud|/products/suite|messaging-hub|agent-desktop|agentexp)"), "Contact Center"),
    (re.compile(r"(egain-for-|egain-solve-for|-for-salesforce|-for-servicenow|-for-sap|microsoft-dynamics|amazon-connect|genesys|five9|talkdesk|avaya|cisco|ibm-watson|adobe|webex|apple-business-chat|safeswitch)"), "Ecosystem / Integrations"),
    (re.compile(r"(field-service)"), "AI Knowledge for Field Service"),
    (re.compile(r"(knowledge-management|knowledge-ai|what-is-km|km-tools|knowledge-academy|/knowledge|ai-knowledge-services|kmai|knowledge-in-the-flow)"), "Knowledge Management"),
    (re.compile(r"^/(de/)?(products|solutions)(/|$)"), "Products & Solutions (overview)"),
]

# --------------------------------------------------------------------------
# Industries / verticals.
# --------------------------------------------------------------------------
INDUSTRY_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(in-health-insurance|health-insurance|payer)"), "Health Insurance"),
    (re.compile(r"(providers-health-care|healthcare|telehealth|health-care)"), "Healthcare"),
    (re.compile(r"(ai-coach-for-banking|in-banking|/banking)"), "Banking"),
    (re.compile(r"(financial-services|financial-therapist|/finserv)"), "Financial Services"),
    (re.compile(r"(insurance)"), "Insurance"),
    (re.compile(r"(government|public-sector|federal|citizen-service)"), "Government"),
    (re.compile(r"(in-retail|/retail|ecommerce|e-commerce)"), "Retail & eCommerce"),
    (re.compile(r"(manufactur)"), "Manufacturing"),
    (re.compile(r"(utilit|energy)"), "Utilities"),
    (re.compile(r"(telco|telecom)"), "Telecom"),
    (re.compile(r"(tech-industry|in-it/|helpdesk|high-tech|software-industry)"), "Technology & IT"),
    (re.compile(r"(travel-hospitality|travel|hospitality|airline)"), "Travel & Hospitality"),
    (re.compile(r"(outsourcer|bpo)"), "BPO / Outsourcers"),
    (re.compile(r"(in-marketing)"), "Marketing"),
    (re.compile(r"(in-eservice|customer-service-industry)"), "Customer Service"),
]

# --------------------------------------------------------------------------
# Referrer classification.
# --------------------------------------------------------------------------
SRC_DIRECT = "Direct / Unknown"
SRC_INTERNAL = "Internal (egain.com)"
SRC_ORGANIC = "Organic Search"
SRC_AI = "AI Assistant"
SRC_SOCIAL = "Social"
SRC_LINKEDIN = "LinkedIn"
SRC_EMAIL = "Email / Marketing"
SRC_JOBS = "Job Boards"
SRC_OTHER = "Other Referral"

_SEARCH_HOSTS = re.compile(
    r"(^|\.)(google\.[a-z.]+|bing\.com|duckduckgo\.com|search\.yahoo\.[a-z.]+|yandex\.[a-z.]+|baidu\.com|"
    r"m\.baidu\.com|ecosia\.org|search\.brave\.com|qwant\.com|naver\.com|seznam\.cz|startpage\.com|ask\.com)$"
)
_AI_HOSTS = re.compile(r"(^|\.)(chatgpt\.com|openai\.com|perplexity\.ai|claude\.ai|anthropic\.com|gemini\.google\.com|copilot\.microsoft\.com|you\.com|phind\.com)$")
_SOCIAL_HOSTS = re.compile(r"(^|\.)(facebook\.com|twitter\.com|x\.com|t\.co|instagram\.com|reddit\.com|youtube\.com|pinterest\.com|quora\.com|medium\.com)$")
_LINKEDIN_HOSTS = re.compile(r"(linkedin\.com|lnkd\.in|com\.linkedin\.android)")
_EMAIL_HOSTS = re.compile(r"(awstrack\.me|hubspotemail\.net|mimecastprotect\.com|linkprotect\.cudasvc\.com|sendgrid|mailchimp|marketo|eloqua|constantcontact|pardot|list-manage\.com|urldefense)")
_JOB_HOSTS = re.compile(r"(indeed\.[a-z.]+|naukri\.com|glassdoor\.[a-z.]+|monster\.[a-z.]+|linkedin\.com/jobs|shine\.com|ziprecruiter\.com|dice\.com)")
_INTERNAL_HOSTS = re.compile(r"(^|\.)(egain\.com|egain\.cloud|egainweb\.wpengine\.com|knowledge\.ai)(:\d+)?$")

_ANDROID_SEARCH = "com.google.android.googlequicksearchbox"

# --------------------------------------------------------------------------
# Bots.
# --------------------------------------------------------------------------
_BOT_UA = re.compile(
    r"(bot\b|bot/|bots\b|crawl|spider|slurp|scrapy|wget|curl/|libwww|python-requests|python/|aiohttp|httpx|"
    r"okhttp|go-http-client|java/|jakarta|apache-httpclient|lua-resty|unirest|rest-client|guzzle|node-fetch|axios/|"
    r"headless|phantomjs|puppeteer|playwright|selenium|monitor|uptime|pingdom|nagios|check_http|zabbix|newrelic|"
    r"feedparser|feedfetcher|rss|aggregage|archiver|wayback|ia_archiver|semrush|ahrefs|blexbot|serpstat|barkrowler|"
    r"imagesift|dataprovider|mediatoolkit|matchory|sogou|petal|yandex|baidu|seokicks|mj12|dotbot|zoominfo|"
    r"externalagent|gptbot|ccbot|claudebot|anthropic-ai|perplexitybot|applebot|bytespider|amazonbot|"
    r"xml-sitemaps|nl-crawler|alipesnews|blp_bbot|site-audit|siteaudit|linkdex|screaming|validator|nutch|"
    r"masscan|zgrab|nmap|scanner|expanse|censys|palo alto|internet-measurement)",
    re.I,
)
_BOT_NAME = re.compile(
    r"(googlebot|bingbot|yandexbot|baiduspider|duckduckbot|applebot|ahrefsbot|ahrefssiteaudit|semrushbot|blexbot|"
    r"mj12bot|dotbot|petalbot|sogou web spider|bytespider|gptbot|claudebot|ccbot|perplexitybot|amazonbot|"
    r"meta-externalagent|imagesiftbot|serpstatbot|barkrowler|matchorysearch|mediatoolkitbot|nl-crawler|"
    r"alipesnews|blp_bbot|scrapy|feedparser|aggregage|check_http|python|aiohttp|curl|wget|unirest|rest-client|lua-resty)",
    re.I,
)
# Deliberately unhelpful / spoofed agents seen in this corpus.
_SUSPECT_UA = re.compile(r"^(mozilla/5\.0/5\.0|mozilla/5\.0|-|)$", re.I)


def normalize_path(url: str) -> Tuple[str, str]:
    """Split a raw request URL into (path, query)."""
    if not url:
        return "/", ""
    url = url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        cut = url.find("/", 8)
        url = url[cut:] if cut != -1 else "/"
    path, _, query = url.partition("?")
    path = path.split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path += "/"
    return path.lower(), query


def classify_page(path: str) -> str:
    p = path
    for pat, cat in _CATEGORY_RULES:
        if pat.search(p):
            return cat
    if _PROOF_PAT.search(p):
        return CAT_PROOF
    if _BLOG_PAT.search(p):
        return CAT_BLOG
    if match_product(p):
        return CAT_PRODUCT
    if match_industry(p):
        return CAT_INDUSTRY
    return CAT_OTHER


def match_product(path: str) -> Optional[str]:
    for pat, name in PRODUCT_RULES:
        if pat.search(path):
            return name
    return None


def match_industry(path: str) -> Optional[str]:
    for pat, name in INDUSTRY_RULES:
        if pat.search(path):
            return name
    return None


_HOST_RE = re.compile(r"^[a-z]+://([^/?#]+)", re.I)


def referrer_host(ref: str) -> str:
    if not ref or ref == "-":
        return ""
    m = _HOST_RE.match(ref.strip())
    host = m.group(1) if m else ref.strip().split("/")[0]
    return host.lower().strip()


def classify_referrer(host: str) -> str:
    if not host:
        return SRC_DIRECT
    bare = host.split(":")[0]
    if _INTERNAL_HOSTS.search(host) or _INTERNAL_HOSTS.search(bare):
        return SRC_INTERNAL
    if _JOB_HOSTS.search(host):
        return SRC_JOBS
    if _LINKEDIN_HOSTS.search(host):
        return SRC_LINKEDIN
    if _SEARCH_HOSTS.search(bare) or host == _ANDROID_SEARCH:
        return SRC_ORGANIC
    if _AI_HOSTS.search(bare):
        return SRC_AI
    if _SOCIAL_HOSTS.search(bare):
        return SRC_SOCIAL
    if _EMAIL_HOSTS.search(host):
        return SRC_EMAIL
    return SRC_OTHER


def classify_user_agent(ua: str) -> Tuple[int, str]:
    """Return (is_bot, bot_name)."""
    if not ua or ua == "-":
        return 1, "unknown-agent"
    low = ua.lower()
    if _BOT_UA.search(low):
        m = _BOT_NAME.search(low)
        return 1, (m.group(1) if m else "generic-bot")
    if _SUSPECT_UA.match(low.strip()):
        return 1, "truncated-agent"
    return 0, ""
