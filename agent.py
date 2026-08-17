import os
import logging
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

import google.cloud.logging
from google.adk.agents import Agent, SequentialAgent
from google.adk.tools.tool_context import ToolContext

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

cloud_logging_client = google.cloud.logging.Client()
cloud_logging_client.setup_logging()

load_dotenv()

MODEL = os.getenv("MODEL", "gemini-2.5-flash")
DB_URL = os.getenv("DATABASE_URL")          # postgres://user:pass@host:5432/dbname
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
NOTES_DIR = os.getenv("NOTES_DIR", "./notes")  # local dir acting as Notes MCP


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------

def _get_db_conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ---------------------------------------------------------------------------
# Sub-Agent A tools  —  Researcher
# ---------------------------------------------------------------------------

def get_cold_leads(tool_context: ToolContext) -> dict:
    """
    Query PostgreSQL for leads that haven't been contacted in 30+ days.
    Saves results to state['cold_leads'].
    """
    cutoff = datetime.utcnow() - timedelta(days=30)
    try:
        conn = _get_db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, company, industry, email, last_contacted_at, status
            FROM leads
            WHERE (last_contacted_at IS NULL OR last_contacted_at < %s)
              AND status NOT IN ('converted', 'disqualified')
            ORDER BY last_contacted_at ASC NULLS FIRST
            LIMIT 5
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
        conn.close()

        leads = [dict(r) for r in rows]
        # Make timestamps JSON-serialisable
        for lead in leads:
            if lead.get("last_contacted_at"):
                lead["last_contacted_at"] = lead["last_contacted_at"].isoformat()

        tool_context.state["cold_leads"] = leads
        logging.info(f"[Researcher] Found {len(leads)} cold leads.")
        return {"status": "success", "count": len(leads), "leads": leads}

    except Exception as e:
        logging.error(f"[Researcher] DB error: {e}")
        return {"status": "error", "message": str(e)}


def search_industry_trends(tool_context: ToolContext, industry: str) -> dict:
    """
    Search for the latest trends in a given industry using Tavily Search API.
    Appends results to state['industry_trends'].
    """
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": f"latest trends and news in {industry} industry 2025",
                "search_depth": "basic",
                "max_results": 3,
            },
            timeout=15,
        )
        data = response.json()
        snippets = [
            {"title": r.get("title"), "snippet": r.get("content", "")[:300]}
            for r in data.get("results", [])
        ]

        existing = tool_context.state.get("industry_trends", {})
        existing[industry] = snippets
        tool_context.state["industry_trends"] = existing

        logging.info(f"[Researcher] Trends fetched for industry: {industry}")
        return {"status": "success", "industry": industry, "results": snippets}

    except Exception as e:
        logging.error(f"[Researcher] Search error: {e}")
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Sub-Agent B tools  —  Drafter
# ---------------------------------------------------------------------------

def get_brand_voice_template(tool_context: ToolContext, template_name: str = "follow_up") -> dict:
    """
    Load a brand voice template from the Notes directory (acts as Notes MCP).
    Saves to state['brand_template'].
    """
    path = os.path.join(NOTES_DIR, f"{template_name}.txt")
    try:
        with open(path, "r") as f:
            content = f.read()
        tool_context.state["brand_template"] = content
        logging.info(f"[Drafter] Loaded template: {template_name}")
        return {"status": "success", "template": content}
    except FileNotFoundError:
        default = (
            "Hi {name},\n\n"
            "I noticed it's been a while since we last connected. "
            "Given what's happening in the {industry} space — {trend_summary} — "
            "I thought now would be a great time to reconnect.\n\n"
            "Would you be open to a quick call? I have a few ideas that could be relevant for {company}.\n\n"
            "Best,\n[Your Name]"
        )
        tool_context.state["brand_template"] = default
        return {"status": "success", "template": default, "note": "Used default template"}


def save_draft_pitch(tool_context: ToolContext, lead_id: str, pitch: str) -> dict:
    """
    Save the drafted pitch to the Notes directory and update state.
    """
    os.makedirs(NOTES_DIR, exist_ok=True)
    filename = f"pitch_{lead_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
    path = os.path.join(NOTES_DIR, filename)

    try:
        with open(path, "w") as f:
            f.write(pitch)

        drafts = tool_context.state.get("saved_drafts", [])
        drafts.append({"lead_id": lead_id, "file": filename, "pitch": pitch})
        tool_context.state["saved_drafts"] = drafts

        logging.info(f"[Drafter] Pitch saved: {filename}")
        return {"status": "success", "file": filename}
    except Exception as e:
        logging.error(f"[Drafter] Save error: {e}")
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Sub-Agent C tools  —  Scheduler
# ---------------------------------------------------------------------------

def get_available_slots(tool_context: ToolContext) -> dict:
    """
    Fetch available 'Sales Call' calendar slots for the next 7 days.
    In production: connects to Google Calendar MCP.
    Here: returns mock slots for the demo.
    """
    base = datetime.utcnow()
    slots = []
    for day_offset in range(1, 8):
        day = base + timedelta(days=day_offset)
        if day.weekday() < 5:  # Mon–Fri only
            slots.append({
                "date": day.strftime("%A, %B %d %Y"),
                "time": "10:00 AM IST",
                "duration_mins": 30,
            })
            if len(slots) == 3:
                break

    tool_context.state["available_slots"] = slots
    logging.info(f"[Scheduler] Found {len(slots)} available slots.")
    return {"status": "success", "slots": slots}


def update_lead_contacted(tool_context: ToolContext, lead_id: str, pitch: str) -> dict:
    """
    Update last_contacted_at in PostgreSQL and write to the outreach_log table.
    """
    try:
        conn = _get_db_conn()
        cur = conn.cursor()

        cur.execute(
            "UPDATE leads SET last_contacted_at = %s WHERE id = %s",
            (datetime.utcnow(), lead_id),
        )
        cur.execute(
            """
            INSERT INTO outreach_log (id, lead_id, pitch_draft, sent_at, response_received, meeting_booked)
            VALUES (gen_random_uuid(), %s, %s, %s, FALSE, FALSE)
            """,
            (lead_id, pitch, datetime.utcnow()),
        )
        conn.commit()
        conn.close()

        logging.info(f"[Scheduler] DB updated for lead: {lead_id}")
        return {"status": "success", "lead_id": lead_id}
    except Exception as e:
        logging.error(f"[Scheduler] DB update error: {e}")
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Sub-Agent D tools  —  Analyst
# ---------------------------------------------------------------------------

def analyze_outreach_patterns(tool_context: ToolContext) -> dict:
    """
    Query outreach_log to surface patterns: which industries/days get more responses.
    Saves insights to state['outreach_insights'].
    """
    try:
        conn = _get_db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                l.industry,
                COUNT(*) AS total_sent,
                SUM(CASE WHEN ol.response_received THEN 1 ELSE 0 END) AS responses,
                SUM(CASE WHEN ol.meeting_booked THEN 1 ELSE 0 END) AS meetings,
                ROUND(
                    100.0 * SUM(CASE WHEN ol.response_received THEN 1 ELSE 0 END) / COUNT(*), 1
                ) AS response_rate_pct
            FROM outreach_log ol
            JOIN leads l ON l.id = ol.lead_id
            GROUP BY l.industry
            ORDER BY response_rate_pct DESC
            """,
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        tool_context.state["outreach_insights"] = rows
        logging.info(f"[Analyst] Pattern analysis complete: {len(rows)} industries.")
        return {"status": "success", "insights": rows}
    except Exception as e:
        logging.error(f"[Analyst] Analysis error: {e}")
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

researcher_agent = Agent(
    name="researcher",
    model=MODEL,
    description="Finds cold leads from the CRM database and researches recent industry trends for each.",
    instruction="""
    You are the Researcher for a solopreneur's CRM system. Your job has two steps:

    Step 1: Call `get_cold_leads` to retrieve leads that haven't been contacted in 30+ days.
            Review the list saved in state['cold_leads'].

    Step 2: For each unique industry found in the cold leads list, call `search_industry_trends`
            with that industry name to fetch the latest news and trends.

    Once you have completed both steps, summarize what you found:
    - How many cold leads exist
    - Key trends per industry (1-2 bullet points each)

    Do not draft any messages. Your job is research only.
    """,
    tools=[get_cold_leads, search_industry_trends],
    output_key="research_summary",
)


drafter_agent = Agent(
    name="drafter",
    model=MODEL,
    description="Drafts personalized outreach pitches for each cold lead using brand voice templates.",
    instruction="""
    You are the Drafter for a solopreneur's CRM. Your job is to write personalized outreach emails.

    Step 1: Call `get_brand_voice_template` to load the user's writing style and tone.

    Step 2: Using state['cold_leads'] and state['industry_trends'], draft a personalized
            follow-up message for EACH cold lead. The message must:
            - Address the lead by first name
            - Reference their company and industry
            - Weave in 1 relevant industry trend naturally
            - Stay under 150 words
            - Match the brand voice from the template

    Step 3: For each drafted pitch, call `save_draft_pitch` with the lead's id and the pitch text.

    Be conversational, not salesy. Write like a human, not a bot.
    """,
    tools=[get_brand_voice_template, save_draft_pitch],
    output_key="drafting_summary",
)


scheduler_agent = Agent(
    name="scheduler",
    model=MODEL,
    description="Appends available meeting slots to each draft and updates the CRM database.",
    instruction="""
    You are the Scheduler for a solopreneur's CRM. Your job is to finalize outreach and update records.

    Step 1: Call `get_available_slots` to retrieve the next 3 available Sales Call times.

    Step 2: Review state['saved_drafts']. For each draft, append a natural-sounding
            meeting invitation at the end of the pitch. Example:
            "I have a 30-min slot open on {date} at {time} — would that work for you?"

    Step 3: For each lead, call `update_lead_contacted` with their lead_id and the final pitch
            (draft + meeting slot appended) to update the database.

    Confirm how many leads were processed and slots proposed.
    """,
    tools=[get_available_slots, update_lead_contacted],
    output_key="scheduling_summary",
)


analyst_agent = Agent(
    name="analyst",
    model=MODEL,
    description="Analyzes historical outreach data to surface patterns and improve future pitches.",
    instruction="""
    You are the Analyst for a solopreneur's CRM.

    Step 1: Call `analyze_outreach_patterns` to query historical outreach logs.

    Step 2: Interpret the data in state['outreach_insights'] and produce a short insight report:
            - Which industries have the highest response rates?
            - Any patterns worth noting?
            - One actionable recommendation for the next outreach batch.

    Keep the report concise — 3 to 5 bullet points maximum.
    """,
    tools=[analyze_outreach_patterns],
    output_key="analyst_report",
)


# ---------------------------------------------------------------------------
# Root agent  —  Business Manager (SequentialAgent)
# ---------------------------------------------------------------------------

outreach_workflow = SequentialAgent(
    name="outreach_workflow",
    description="End-to-end cold lead outreach pipeline: research → draft → schedule → analyze.",
    sub_agents=[
        researcher_agent,
        drafter_agent,
        scheduler_agent,
        analyst_agent,
    ],
)

root_agent = Agent(
    name="business_manager",
    model=MODEL,
    description="The primary orchestrator for SoloPitch — a solopreneur CRM and outreach engine.",
    instruction="""
    You are the Business Manager for a solopreneur's AI-powered CRM system called SoloPitch.

    When the user asks you to run outreach, find cold leads, or manage follow-ups,
    transfer control to the 'outreach_workflow' agent which will:
      1. Find cold leads and research their industries
      2. Draft personalized pitches in the user's brand voice
      3. Append available meeting slots and update the CRM
      4. Analyze historical patterns for insights

    After the workflow completes, present a clean summary to the user:
    - Number of cold leads processed
    - Pitches drafted and saved
    - Meeting slots proposed
    - Top insight from the Analyst

    If the user asks about specific leads, trends, or results, answer using
    the data in the conversation state. Always be concise and business-like.
    """,
    sub_agents=[outreach_workflow],
)
