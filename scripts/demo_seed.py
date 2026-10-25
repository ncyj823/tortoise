"""Idempotent demo seed: one fictional Brightleaf tenant and three sample campaigns.

Run with: python scripts/demo_seed.py
If the tenant already exists, nothing is written.
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_team import core, offerings
from tests.fixtures import (BRAIN, BRIGHTLEAF_SUB, COMPANY, LANGUAGE, SOURCE_URL, SUMMARY,
                            TID, TIMEZONE)


PLAYBOOKS = {
    agent: {
        "mission": f"Create {agent} drafts for {COMPANY}, always for human approval.",
        "steps": ["Read the brief", "Draft", "Check the claim bank", "Save as a draft"],
        "tools": ["Company Brain", "Protected offering rules"],
        "output_format": "Plain text with clear sections",
        "quality_checklist": ["Every product line is from the claim bank", "No banned terms"],
        "never_do": ["Never publish", "Never invent a price or a fact"],
        "schedule": TIMEZONE,
        "kpis": ["Approved drafts"],
    }
    for agent in core.PLAYBOOK_AGENTS
}


def video(seconds, ratios, line, on_screen, caption, shots):
    return {
        "duration_seconds": seconds,
        "aspect_ratios": ratios,
        "scenes": [{"line": line, "on_screen_text": on_screen, "visual": "[WHAT THE USER SEES]"}],
        "caption": caption,
        "shot_list": shots,
    }


def post(platform, text):
    return {"platform": platform, "text": text, "hashtags": ["BrightleafCoffee"], "notes": ""}


def package(request, goal, audience, core_message, call_to_action, videos, posts, questions=None):
    return {
        "campaign_summary": {
            "goal": goal,
            "audience": audience,
            "core_message": core_message,
            "call_to_action": call_to_action,
        },
        "videos": videos,
        "posts": posts,
        "article": None,
        "sources": [{"claim": "Brightleaf Coffee product page", "quote": SUMMARY, "source": SOURCE_URL}],
        "open_questions": questions or [],
        "notes": ["Sample data is fictional (Brightleaf Coffee). Every item is a draft."],
    }


def sample_campaigns():
    claim = BRIGHTLEAF_SUB.claim_bank
    return [
        (
            "Facebook ad campaign for the Brightleaf Subscription. Give me the videos and structure.",
            package(
                request="Facebook ad campaign for the Brightleaf Subscription. Give me the videos and structure.",
                goal="Subscriptions",
                audience="coffee lovers",
                core_message=claim[0],
                call_to_action="[CTA]",
                videos={
                    "hook": video(5, ["9:16", "4:5"], claim[1], "Roast date on every bag",
                                  "Fresh coffee, on your schedule.", ["Close-up of a bag", "Roast date label"]),
                    "short": video(30, ["9:16", "4:5"], claim[2], "Pause or cancel anytime",
                                   "Coffee that fits your week.", ["Kitchen scene", "Bag on the counter"]),
                    "explainer": video(120, ["16:9"], claim[4], "Whole bean or ground",
                                       "Choose the grind that suits you.", ["Subscription page", "Grind choice"]),
                },
                posts=[post("facebook", claim[0])],
            ),
        ),
        (
            "Instagram launch posts for the Brightleaf Subscription.",
            package(
                request="Instagram launch posts for the Brightleaf Subscription.",
                goal="Awareness",
                audience="home coffee drinkers",
                core_message=claim[1],
                call_to_action="[CTA]",
                videos={
                    "hook": video(5, ["9:16"], claim[3], "Free shipping above the threshold",
                                  "Good coffee, delivered.", ["Parcel on a doorstep", "Bag opening"]),
                    "short": video(30, ["9:16", "4:5"], claim[2], "Pause or cancel anytime",
                                   "No lock-in, just coffee.", ["Phone with subscription page"]),
                    "explainer": video(120, ["16:9"], claim[0], "Choose your schedule",
                                       "A coffee routine that follows your week.", ["Calendar", "Brewing coffee"]),
                },
                posts=[post("instagram", claim[1]), post("instagram", claim[3])],
            ),
        ),
        (
            "Explain the Brightleaf Subscription in a short video.",
            package(
                request="Explain the Brightleaf Subscription in a short video.",
                goal="Consideration",
                audience="new subscribers",
                core_message=claim[4],
                call_to_action="[CTA]",
                videos={
                    "hook": video(5, ["9:16", "4:5"], claim[0], "Fresh roast, your schedule",
                                  "Start with the coffee you like.", ["Bag on a table"]),
                    "short": video(30, ["9:16", "4:5"], claim[2], "Pause or cancel anytime",
                                   "Try it without a long commitment.", ["Phone screen"]),
                    "explainer": video(120, ["16:9"], claim[1], "Roast date on every bag",
                                       "Know exactly when your coffee was roasted.", ["Bag label", "Coffee pour"]),
                },
                posts=[post("linkedin", claim[0])],
            ),
        ),
    ]


def main():
    core.init_db()
    if core.get_tenant(TID):
        print(f"{TID} already exists, nothing to do")
        return

    now = dt.datetime.utcnow().isoformat()
    with core.db() as c:
        c.execute(
            "INSERT INTO tenants(id,work_email,user_name,user_role,timezone,language,website,status,error,"
            "brain,playbooks,welcome,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                TID,
                f"founder@{TID}",
                "Demo founder",
                "Founder",
                TIMEZONE,
                LANGUAGE,
                "https://brightleaf.example",
                "ready",
                None,
                json.dumps(BRAIN, ensure_ascii=False),
                json.dumps(PLAYBOOKS, ensure_ascii=False),
                "Welcome to the Brightleaf Coffee demo. Every item is a draft for human approval.",
                now,
            ),
        )
        for request, pkg in sample_campaigns():
            content = {"request": request, "intent": {}, "package": pkg}
            c.execute(
                "INSERT INTO outputs(tenant_id,agent,content,created_at,status) VALUES(?,?,?,?,?)",
                (TID, "content", json.dumps(content, ensure_ascii=False), now, "draft"),
            )

    offerings.save(TID, BRIGHTLEAF_SUB, None)
    print(f"Seeded {TID} with 3 sample campaigns")


if __name__ == "__main__":
    main()
