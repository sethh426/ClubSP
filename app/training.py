"""Internal curriculum and owner-assessed practice; no model or live campaign."""
from uuid import UUID, uuid4
import json

from core.memory.models import utc_now
from .validation import text_field, list_field


CURRICULUM_VERSION = "2026-09-30.1"
RUBRIC = {
    "listening": "Acknowledge the concern without guessing a motive.",
    "grounding": "Use only supported facts and state missing evidence.",
    "clarity": "Explain the next step in plain language.",
    "next_step": "Offer one appropriate action or stop when requested.",
    "economics": "Stay inside recorded financial limits without promises.",
    "respect": "Respect refusal, privacy and independent review.",
}
HARD_FAILURES = {
    "fabricated_promise": "Invented funding, title, value or closing certainty",
    "hidden_role": "Hidden or misstated acquisition/assignment role",
    "ignored_stop": "Continued solicitation after a stop request",
    "unauthorized_terms": "Changed terms without owner authority",
    "legal_improvisation": "Invented legal clearance or advice",
}


def lesson(key, title, audience, prompt, question, guidance, avoid, next_step):
    return {"id": key, "title": title, "audience": audience, "prompt": prompt,
            "clarifying_question": question, "guidance": guidance, "avoid": avoid,
            "next_step": next_step, "version": CURRICULUM_VERSION,
            "source_reference": "docs/SALES_AND_COMMUNICATION_TRAINING.md",
            "status": "Internal draft curriculum; review before external use",
            "scenario_kind": "synthetic"}


LESSONS = [
    lesson("discovery", "Discover the seller's priorities", "Seller",
           "SYNTHETIC: I might sell, but I haven't decided what I want to do.",
           "What would a good outcome look like for you?",
           "Ask permission and listen before discussing terms. Confirm goals, timing, alternatives and decision participants from the person's statements.",
           "Do not assume urgency, distress or ownership from an address or public record.",
           "Confirm one priority and agree whether another conversation would help."),
    lesson("price", "Price expectations", "Seller",
           "SYNTHETIC: Your number is too low. Someone else said it is worth more.",
           "Is price the main concern, or are there terms that also matter?",
           "Acknowledge the gap. Explain recorded assumptions only if they exist. Compare evidence without criticizing an unverified competing offer.",
           "Do not invent defects, change the ceiling or claim an estimate is an appraisal.",
           "Request evidence for the competing terms and review economics before revising anything."),
    lesson("timing", "Timing and certainty", "Seller",
           "SYNTHETIC: Can you promise this will close next Friday?",
           "What timing would work, and what makes that date important?",
           "Confirm preferences and separate a desired date from a verified closing schedule. Explain remaining conditions accurately.",
           "No guaranteed closing date, funding or waived diligence.",
           "Record the timing and ask the established closing contact to review feasibility."),
    lesson("trust", "Trust and transparency", "Seller",
           "SYNTHETIC: How do I know you are legitimate?",
           "What would you like to verify before continuing?",
           "Explain your actual role and provide verifiable identity and process information. Encourage independent review.",
           "No fake credentials, testimonials, relationships or pressure to skip advice.",
           "Offer the approved identity/process details for independent checking."),
    lesson("role", "Explain the transaction role", "Seller",
           "SYNTHETIC: Are you buying this yourself or assigning a contract?",
           "Would it help to review the proposed structure in writing?",
           "State the actual intended role and reviewed structure. If those details are not approved, obtain review before answering with terms.",
           "Do not imply a funded direct purchase or hide an intended assignment.",
           "Use the reviewed jurisdiction-specific explanation and invite independent advice."),
    lesson("authority", "Other decision participants", "Seller",
           "SYNTHETIC: My sibling also has an interest in the property.",
           "Who else needs to participate in a decision?",
           "Ask neutrally about authority and required participants. Record what was said separately from professional verification.",
           "Do not treat a conversation or owner-of-record field as proof of sole authority.",
           "Route ownership/estate/title questions to the appropriate professional."),
    lesson("condition", "Condition and access", "Seller",
           "SYNTHETIC: The property needs repairs, but I don't want strangers entering.",
           "What issues should an evaluator understand, and what access would you be comfortable with?",
           "Respect access preferences, occupants and privacy. Distinguish seller statements from inspection evidence.",
           "No invented repairs, unapproved access or claims that an inspection already occurred.",
           "Agree a permitted evidence or access step without promising a repair estimate."),
    lesson("finance", "Buyer funding and margin", "Buyer",
           "SYNTHETIC: I can probably get the money. Send me the agreement now.",
           "What funding and review requirements must be satisfied before an agreement?",
           "Separate interest from verified ability. Compare buy-box criteria and costs using dated evidence.",
           "Do not label exploratory interest as verified funds or an executable exit.",
           "Request the agreed funding evidence and identify remaining conditions."),
    lesson("paperwork", "Paperwork and independent review", "Seller",
           "SYNTHETIC: I don't understand this agreement. Can you tell me it is safe?",
           "Which terms would you like the appropriate professional to explain?",
           "Explain the process and refer agreement, disclosure and title questions for qualified independent review.",
           "No legal advice, invented clearance or pressure to sign.",
           "Pause commitments and schedule the appropriate review."),
    lesson("stop", "Respect a stop request", "Seller",
           "SYNTHETIC: Stop contacting me and remove me from your list.",
           "No sales question is appropriate after an explicit stop request.",
           "Record the request, suppress the contact and cancel pending drafts. A neutral acknowledgment, if separately appropriate, contains no solicitation.",
           "No objection handling, alternative channel or request to reconsider.",
           "Stop contact and preserve the suppression evidence."),
    lesson("legal", "Unresolved jurisdiction requirements", "Seller",
           "SYNTHETIC: Is assigning this agreement allowed here?",
           "Which proposal and jurisdiction need professional review?",
           "Do not substitute a historical article or template for a current reviewed policy. Preserve the question as an unresolved condition.",
           "No invented law, disclosure text or certification of legality.",
           "Obtain current official materials and professional review before proceeding."),
    lesson("payment_change", "Changed payment instructions", "Closing",
           "SYNTHETIC: Please send the deposit to these new wire details today.",
           "Use the established verified closing contact to check this independently.",
           "Pause payment changes. Verify through the previously established contact and record the result.",
           "Do not copy new bank details into a reply or treat the email as verification.",
           "Escalate to the owner and established closing professional."),
]


class TrainingMixin:
    def _training_state(self, connection):
        attempts = []
        for row in connection.execute("SELECT * FROM practice_attempts ORDER BY created_at DESC,id"):
            record = dict(row)
            record["assessment"] = json.loads(record.pop("assessment_json"))
            attempts.append(record)
        return {"version": CURRICULUM_VERSION, "lessons": LESSONS, "rubric": RUBRIC,
                "hard_failures": HARD_FAILURES, "attempts": attempts,
                "assessment_method": "Owner self-assessment, not an automated evaluation or prediction of sales success"}

    def save_practice(self, data):
        scenario_id = text_field(data, "scenario_id", 40)
        if scenario_id not in {l["id"] for l in LESSONS}:
            raise ValueError("Unknown training scenario")
        response = text_field(data, "response", 8000)
        note = text_field(data, "review_note", 1000)
        try:
            key = str(UUID(text_field(data, "attempt_key", 36)))
        except ValueError as exc:
            raise ValueError("attempt_key must be a UUID") from exc
        ratings = data.get("ratings")
        if not isinstance(ratings, dict) or set(ratings) != set(RUBRIC):
            raise ValueError("Rate all six training dimensions")
        if any(isinstance(v, bool) or not isinstance(v, int) or v not in {0, 1, 2} for v in ratings.values()):
            raise ValueError("Training ratings must be 0, 1 or 2")
        failures = list_field(data, "hard_failures", allowed=HARD_FAILURES, required=False, max_items=5)
        total = sum(ratings.values())
        assessment = {"ratings": ratings, "hard_failures": failures, "total": total,
                      "meets_practice_threshold": total >= 10 and not failures,
                      "method": "owner_self_assessment", "external_template_approved": False}
        with self.database.session(write=True) as (connection, _):
            existing = connection.execute("SELECT * FROM practice_attempts WHERE attempt_key=?", (key,)).fetchone()
            if existing:
                if (existing["scenario_id"], existing["response"], json.loads(existing["assessment_json"]), existing["review_note"]) != (scenario_id, response, assessment, note):
                    raise ValueError("attempt_key already records a different practice response")
                result = dict(existing)
                result["assessment"] = json.loads(result.pop("assessment_json"))
                return result
            record = {"id": str(uuid4()), "attempt_key": key, "scenario_id": scenario_id,
                      "curriculum_version": CURRICULUM_VERSION, "response": response,
                      "assessment_json": json.dumps(assessment), "review_note": note,
                      "created_at": utc_now().isoformat()}
            connection.execute("INSERT INTO practice_attempts("+",".join(record)+") VALUES("+",".join("?" for _ in record)+")", tuple(record.values()))
            record["assessment"] = assessment
            del record["assessment_json"]
            return record
