import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db, safe_database_url
from app.models import CallRecord
from app.schemas import CallCreate
from app.services.vapi import get_call_details, initiate_call

router = APIRouter(prefix="/api")

FIXED_CALL_QUESTIONS = [
    "What is your name?",
    "What is your age?",
    "Which city are you from?",
    "What do you do for work?",
    "Are you interested in AI tools?",
]


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def normalize_status(value: str | None) -> str:
    if not value:
        return ""

    normalized = re.sub(r"[\s_-]+", " ", str(value).strip().lower())
    status_map = {
        "pending": "Pending",
        "queued": "Queued",
        "running": "In Progress",
        "in progress": "In Progress",
        "completed": "Completed",
        "ended": "Ended",
        "failed": "Failed",
        "failedtoqueue": "FailedToQueue",
        "failed to queue": "FailedToQueue",
        "end of call report": "Completed",
    }
    return status_map.get(normalized, str(value).strip().title())


def extract_vapi_status(data: dict[str, Any]) -> str | None:
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    call = message.get("call") if isinstance(message.get("call"), dict) else {}

    status = (
        data.get("status")
        or message.get("status")
        or call.get("status")
    )
    if isinstance(status, str):
        return normalize_status(status)

    message_type = message.get("type")
    if message_type == "end-of-call-report":
        return "Completed"
    return None


def extract_vapi_duration(data: dict[str, Any]) -> str | None:
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    call = message.get("call") if isinstance(message.get("call"), dict) else {}

    duration = (
        data.get("durationSeconds")
        or data.get("duration")
        or message.get("durationSeconds")
        or message.get("duration")
        or call.get("durationSeconds")
        or call.get("duration")
    )
    return str(duration) if duration not in (None, "") else None


def split_transcript_lines(transcript: str | None) -> list[str]:
    """Split and parse transcript into speaker: text lines."""
    if not transcript:
        return []

    raw_lines = []
    for chunk in transcript.split("||"):
        for line in str(chunk).splitlines():
            clean_line = line.strip()
            if clean_line:
                raw_lines.append(clean_line)

    lines = []
    pending_speaker = None
    timestamp_pattern = re.compile(r"^\d{1,2}:\d{2}:\d{2}\s*(AM|PM)?\s*\(\+\d{2}:\d{2}\.\d+\)$", re.IGNORECASE)
    speaker_pattern = re.compile(r"^(assistant|ai|user|customer)\s*[:\s]\s*(.+)$", re.IGNORECASE)

    for line in raw_lines:
        # Skip timestamps
        if timestamp_pattern.match(line):
            continue
        
        # Check if line already has speaker: text format
        speaker_match = speaker_pattern.match(line)
        if speaker_match:
            speaker = speaker_match.group(1).lower()
            text = speaker_match.group(2).strip()
            lines.append(f"{speaker}: {text}")
            pending_speaker = None
            continue
        
        # Handle standalone speaker labels
        if line.lower() in {"assistant", "ai", "user", "customer"}:
            pending_speaker = line.lower()
            continue
        
        # Attach text to pending speaker or add as standalone
        if pending_speaker and line:
            lines.append(f"{pending_speaker}: {line}")
            pending_speaker = None
        elif line:  # Skip empty lines
            lines.append(line)

    return lines


def parse_speaker_line(line: str) -> tuple[str | None, str]:
    speaker_match = re.match(r"^(assistant|ai|user|customer)\s*:\s*(.+)$", line, re.IGNORECASE)
    if speaker_match:
        return speaker_match.group(1).lower(), speaker_match.group(2).strip()
    return None, line.strip()


def extract_responses_from_transcript(questions: list[str], transcript: str | None) -> list[str]:
    lines = split_transcript_lines(transcript)
    if not questions or not lines:
        return []

    responses = []
    search_start = 0

    for question in questions:
        normalized_question = normalize_text(question)
        answer = ""

        for index in range(search_start, len(lines)):
            speaker, text = parse_speaker_line(lines[index])
            normalized_text = normalize_text(text)
            is_assistant_question = speaker in {"assistant", "ai", None} and normalized_question in normalized_text
            if not is_assistant_question:
                continue

            for answer_index in range(index + 1, len(lines)):
                answer_speaker, answer_text = parse_speaker_line(lines[answer_index])
                if answer_speaker in {"user", "customer"}:
                    answer = answer_text
                    search_start = answer_index + 1
                    break
                if answer_speaker in {"assistant", "ai"}:
                    break
            break

        responses.append(answer)

    return responses


def call_value(call: CallRecord | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(call, dict):
        return call.get(key, default)
    return getattr(call, key, default)


def set_call_value(call: CallRecord | dict[str, Any], key: str, value: Any) -> None:
    if isinstance(call, dict):
        call[key] = value
    else:
        setattr(call, key, value)


def serialize_call(call: CallRecord | dict[str, Any]) -> dict:
    questions = call_value(call, "questions", "").split("||") if call_value(call, "questions") else []
    responses = call_value(call, "responses", "").split("||") if call_value(call, "responses") else []
    
    # Parse and clean transcript
    transcript = call_value(call, "transcript")
    transcript_lines = split_transcript_lines(transcript) if transcript else []
    
    # Extract responses if not already available
    if not any(r.strip() for r in responses) and transcript_lines and questions:
        responses = extract_responses_from_transcript(questions, transcript)

    return {
        "id": call_value(call, "id"),
        "name": call_value(call, "name", ""),
        "phone": call_value(call, "phone", ""),
        "status": call_value(call, "status", "Pending"),
        "duration": call_value(call, "duration") or "Pending",
        "externalId": call_value(call, "external_id"),
        "questionsAsked": questions,
        "responses": responses,
        "transcript": transcript_lines,
        "createdAt": call_value(call, "created_at") or "",
    }


def extract_vapi_call_id(data: dict[str, Any]) -> str | None:
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    call = message.get("call") if isinstance(message.get("call"), dict) else {}

    call_id = (
        data.get("callId")
        or data.get("externalId")
        or data.get("id")
        or message.get("callId")
        or message.get("call_id")
        or call.get("id")
    )
    return str(call_id) if call_id else None


def extract_vapi_transcript(data: dict[str, Any]) -> str | None:
    """Extract and format transcript from VAPI webhook data."""
    message = data.get("message") if isinstance(data.get("message"), dict) else data
    artifact = message.get("artifact") if isinstance(message.get("artifact"), dict) else {}

    # Try direct transcript fields
    transcript = (
        data.get("transcript")
        or message.get("transcript")
        or artifact.get("transcript")
    )
    if isinstance(transcript, list):
        # Clean and format each transcript line
        formatted = []
        for item in transcript:
            item_str = str(item).strip()
            if item_str and not re.match(r"^\d{1,2}:\d{2}:\d{2}", item_str):
                formatted.append(item_str)
        if formatted:
            return "||".join(formatted)
    elif transcript:
        return str(transcript).strip()

    # Try messages array format
    messages = artifact.get("messages") or message.get("messages")
    if isinstance(messages, list):
        lines = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = (item.get("role") or item.get("type") or "message").lower()
            text = (item.get("message") or item.get("text") or item.get("content") or "").strip()
            if text:
                lines.append(f"{role}: {text}")
        if lines:
            return "||".join(lines)

    # Try single message event
    event_type = message.get("type")
    role = (message.get("role") or "user").lower()
    text = (message.get("text") or message.get("content") or "").strip()
    if event_type == "transcript" and text:
        return f"{role}: {text}"

    return None


def refresh_call_from_vapi(call: CallRecord | dict[str, Any], db: Session | None = None) -> None:
    if not call_value(call, "external_id"):
        return

    ret = get_call_details(str(call_value(call, "external_id")))
    if not ret.get("ok"):
        return

    body = ret.get("body") or {}
    transcript = extract_vapi_transcript(body)
    changed = False
    if transcript and not call_value(call, "transcript"):
        set_call_value(call, "transcript", transcript)
        questions = call_value(call, "questions", "").split("||") if call_value(call, "questions") else FIXED_CALL_QUESTIONS
        responses = extract_responses_from_transcript(questions, transcript)
        if any(responses):
            set_call_value(call, "responses", "||".join(responses))
        changed = True

    status = extract_vapi_status(body)
    if status:
        set_call_value(call, "status", status)
        changed = True

    duration_seconds = extract_vapi_duration(body)
    if duration_seconds:
        set_call_value(call, "duration", duration_seconds)
        changed = True

    if changed and db:
        db.commit()
        db.refresh(call)


@router.post("/vapi-webhook")
async def vapi_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle VAPI webhook for transcript and call updates."""
    data = await request.json()
    call_id = extract_vapi_call_id(data)
    transcript = extract_vapi_transcript(data)
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    
    print(f"Vapi webhook received: type={message.get('type')} call_id={call_id} has_transcript={bool(transcript)}")
    
    if not call_id:
        return {"ok": False, "error": "Missing callId"}

    call = db.execute(
        select(CallRecord).where(CallRecord.external_id == str(call_id))
    ).scalar_one_or_none()

    if call:
        if transcript:
            if call.transcript:
                existing_lines = set(call.transcript.split("||"))
                new_lines = transcript.split("||") if isinstance(transcript, str) else [transcript]
                for line in new_lines:
                    if line.strip() and line.strip() not in existing_lines:
                        call.transcript = f"{call.transcript}||{line}"
            else:
                call.transcript = transcript

            questions = call.questions.split("||") if call.questions else FIXED_CALL_QUESTIONS
            responses = extract_responses_from_transcript(questions, call.transcript)
            if any(r.strip() for r in responses):
                call.responses = "||".join(responses)

        status = extract_vapi_status(data)
        if status:
            call.status = status

        duration = extract_vapi_duration(data)
        if duration:
            call.duration = duration

        db.commit()
        db.refresh(call)

    return {"ok": True, "id": call.id if call else None, "stored": bool(call)}


@router.post("/start-call")
def start_call(data: CallCreate, db: Session = Depends(get_db)):
    next_id = (db.execute(select(func.max(CallRecord.id))).scalar() or 0) + 1
    new_call = CallRecord(
        id=next_id,
        name=data.name,
        phone=data.phone,
        status="Pending",
        duration="Pending",
        questions="||".join(FIXED_CALL_QUESTIONS),
        responses="",
        transcript="",
        created_at=new_call_timestamp(),
        external_id=None,
    )

    try:
        ret = initiate_call(phone=new_call.phone, name=new_call.name)
        if ret.get("ok"):
            body = ret.get("body") or {}
            ext_id = body.get("id") or body.get("call_id") or body.get("external_id")
            new_call.external_id = ext_id
            new_call.status = "Queued"
        else:
            err = ret.get("error")
            new_call.status = "FailedToQueue"
            new_call.responses = f"vapi_error:{err}"
    except Exception as exc:
        new_call.status = "FailedToQueue"
        new_call.responses = f"vapi_exception:{str(exc)}"

    db.add(new_call)
    db.commit()
    db.refresh(new_call)
    message = "Call queued" if new_call.status == "Queued" else "Call failed to queue"
    return {"message": message, "data": serialize_call(new_call)}


@router.get("/db-health")
def db_health(db: Session = Depends(get_db)):
    count = db.query(CallRecord).count()
    latest = db.execute(
        select(CallRecord).order_by(CallRecord.id.desc()).limit(1)
    ).scalar_one_or_none()
    return {
        "ok": True,
        "databaseUrl": safe_database_url(),
        "callsCount": count,
        "latestCallId": latest.id if latest else None,
    }


@router.get("/calls")
def get_calls(db: Session = Depends(get_db)):
    calls = db.execute(
        select(CallRecord).order_by(CallRecord.id.desc())
    ).scalars().all()
    for call in calls:
        refresh_call_from_vapi(call, db)
    return [
        serialize_call(call)
        for call in calls
    ]


@router.get("/calls/{call_id}")
def get_call(call_id: int, db: Session = Depends(get_db)):
    call = db.get(CallRecord, call_id)
    if not call:
        return {
            "id": call_id,
            "name": "Temporary call",
            "phone": "",
            "status": "Pending",
            "duration": "Pending",
            "externalId": None,
            "questionsAsked": FIXED_CALL_QUESTIONS,
            "responses": [],
            "transcript": [],
            "createdAt": "",
        }

    refresh_call_from_vapi(call, db)
    return serialize_call(call)


@router.post("/calls/{call_id}/end")
def end_call_endpoint(call_id: int, db: Session = Depends(get_db)):
    """End or cancel an active call."""
    from app.services.vapi import end_call

    call = db.get(CallRecord, call_id)
    if not call:
        call = CallRecord(
            id=call_id,
            name="Temporary call",
            phone="",
            status="Ended",
            duration="Pending",
            questions="||".join(FIXED_CALL_QUESTIONS),
            responses="",
            transcript="",
            created_at=new_call_timestamp(),
            external_id=None,
        )
        db.add(call)

    if call.external_id and call.status in ["Queued", "Pending", "In Progress"]:
        end_call(str(call.external_id))

    call.status = "Ended"
    db.commit()
    db.refresh(call)
    return {"message": "Call ended", "data": serialize_call(call)}


def new_call_timestamp() -> str:
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %I:%M %p')
