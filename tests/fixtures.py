"""Shared fictional demo company and offering rules for the tests."""
from agent_team import offerings

TID = "brightleaf.example"
COMPANY = "Brightleaf Coffee"
SUMMARY = "Fresh roasted coffee, delivered."
SOURCE_URL = "https://brightleaf.example/products"
LANGUAGE = "English"
TIMEZONE = "Asia/Kolkata"

BRIGHTLEAF_SUB = offerings.OfferingRule(
    name="Brightleaf Subscription",
    facts=[
        "Brightleaf Subscription is a coffee subscription from Brightleaf Coffee.",
        "It delivers freshly roasted coffee on a delivery schedule the customer chooses.",
        "The roast date is printed on every bag.",
        "Customers can pause or cancel the subscription at any time.",
        "Orders above the stated threshold ship free.",
        "Coffee can be ordered as whole bean or ground.",
    ],
    not_yet_available=["Same-day delivery"],
    copy_rules=["Do not describe the roasting equipment or the farms."],
    never_claim_terms=["Starbucks", "organic certified"],
    claim_bank=[
        "Brightleaf Subscription, from Brightleaf Coffee, delivers freshly-roasted coffee on the schedule you choose.",
        "Every bag of Brightleaf Subscription coffee has the roast date printed on it.",
        "You can pause or cancel your Brightleaf Subscription at any time.",
        "Brightleaf Subscription orders above the stated threshold ship free.",
        "Brightleaf Subscription coffee comes as whole bean or ground, whichever you prefer.",
    ],
    source=SOURCE_URL,
)

BRAIN = {
    "company": {"name": COMPANY, "one_line_summary": SUMMARY},
    "offerings": [{"name": "Brightleaf Beans", "description": "Bags of whole bean coffee."}],
    "positioning": {"value_proposition": SUMMARY},
    "brand_voice": {"tone_adjectives": ["warm", "plain-language"]},
    "sources": [{"url": SOURCE_URL, "used_for": "products"}],
}
