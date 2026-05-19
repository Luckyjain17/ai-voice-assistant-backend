import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.schemas import CallCreate
from app.database import SessionLocal
from app.models import CallRecord
from app.services.vapi import get_call_details, initiate_call

router = APIRouter(prefix="/api")
STALE_ACTIVE_CALL_MINUTES = 30
ACTIVE_CALL_STATUSES = {"pending", "queued", "in progress", "in-progress", "running"}

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
    }
    return status_map.get(normalized, str(value).strip().title())


def parse_call_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    for date_format in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue

    return None


def mark_stale_active_call_failed(call: CallRecord, db) -> None:
    status = normalize_status(call.status)
    if status.lower() not in ACTIVE_CALL_STATUSES:
        if status and status != call.status:
            call.status = status
            db.commit()
            db.refresh(call)
        return

    created_at = parse_call_timestamp(call.created_at)
    if not created_at:
        return

    if datetime.now() - created_at <= timedelta(minutes=STALE_ACTIVE_CALL_MINUTES):
        if status != call.status:
            call.status = status
            db.commit()
            db.refresh(call)
        return

    call.status = "Failed"
    stale_message = f"Call marked failed after {STALE_ACTIVE_CALL_MINUTES} minutes without completion."
    responses = call.responses or ""
    if stale_message not in responses:
        call.responses = responses + ("||" if responses else "") + stale_message
    db.commit()
    db.refresh(call)


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


def serialize_call(call: CallRecord) -> dict:
    questions = call.questions.split("||") if call.questions else []
    responses = call.responses.split("||") if call.responses else []
    
    # Parse and clean transcript
    transcript_lines = split_transcript_lines(call.transcript) if call.transcript else []
    
    # Extract responses if not already available
    if not any(r.strip() for r in responses) and transcript_lines and questions:
        responses = extract_responses_from_transcript(questions, call.transcript)

    return {
        "id": call.id,
        "name": call.name,
        "phone": call.phone,
        "status": call.status,
        "duration": call.duration or "Pending",
        "externalId": call.external_id,
        "questionsAsked": questions,
        "responses": responses,
        "transcript": transcript_lines,
        "createdAt": call.created_at or "",
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


def refresh_call_from_vapi(call: CallRecord, db) -> None:
    if not call.external_id or call.transcript:
        return

    ret = get_call_details(str(call.external_id))
    if not ret.get("ok"):
        return

    body = ret.get("body") or {}
    transcript = extract_vapi_transcript(body)
    if not transcript:
        return

    call.transcript = transcript
    questions = call.questions.split("||") if call.questions else FIXED_CALL_QUESTIONS
    responses = extract_responses_from_transcript(questions, call.transcript)
    if any(responses):
        call.responses = "||".join(responses)

    status = body.get("status")
    if status:
        call.status = normalize_status(str(status))

    duration_seconds = body.get("durationSeconds") or body.get("duration")
    if duration_seconds:
        call.duration = str(duration_seconds)

    db.commit()
    db.refresh(call)


@router.post("/vapi-webhook")
async def vapi_webhook(request: Request):
    """Handle VAPI webhook for transcript and call updates."""
    data = await request.json()
    call_id = extract_vapi_call_id(data)
    transcript = extract_vapi_transcript(data)
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    
    print(f"Vapi webhook received: type={message.get('type')} call_id={call_id} has_transcript={bool(transcript)}")
    
    if not call_id:
        return {"ok": False, "error": "Missing callId"}

    with SessionLocal() as db:
        call = db.query(CallRecord).filter(CallRecord.external_id == str(call_id)).first()
        if not call:
            return {"ok": False, "error": "Call not found for external_id"}
        
        # Only process if we have transcript updates
        if transcript:
            # Accumulate transcript lines, avoiding duplicates
            if call.transcript:
                existing_lines = set(call.transcript.split("||"))
                new_lines = transcript.split("||") if isinstance(transcript, str) else [transcript]
                # Only add new lines not already in transcript
                for line in new_lines:
                    if line.strip() and line.strip() not in existing_lines:
                        call.transcript = f"{call.transcript}||{line}"
            else:
                call.transcript = transcript

            # Extract responses from accumulated transcript
            questions = call.questions.split("||") if call.questions else FIXED_CALL_QUESTIONS
            responses = extract_responses_from_transcript(questions, call.transcript)
            if any(r.strip() for r in responses):
                call.responses = "||".join(responses)
        
        # Update status if provided
        status = message.get("status") or data.get("status")
        if status and isinstance(status, str):
            call.status = normalize_status(status)

        db.commit()
        db.refresh(call)
        return {"ok": True, "id": call.id}


@router.post("/start-call")
def start_call(data: CallCreate):
    with SessionLocal() as db:
        new_call = CallRecord(
            name=data.name,
            phone=data.phone,
            status="Pending",
            duration="Pending",
            questions="||".join(FIXED_CALL_QUESTIONS),
            responses="",
            transcript="",
            created_at=new_call_timestamp(),
        )
        db.add(new_call)
        db.commit()
        db.refresh(new_call)

        # Try to enqueue with VAPI AI (best-effort). Update record with external id/status.
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
                new_call.responses = (new_call.responses or "") + ("||" if new_call.responses else "") + f"vapi_error:{err}"
        except Exception as exc:
            new_call.status = "FailedToQueue"
            new_call.responses = (new_call.responses or "") + ("||" if new_call.responses else "") + f"vapi_exception:{str(exc)}"

        db.commit()
        db.refresh(new_call)

        message = "Call queued" if new_call.status == "Queued" else "Call failed to queue"
        return {"message": message, "data": serialize_call(new_call)}


@router.get("/calls")
def get_calls():
    with SessionLocal() as db:
        call_list = db.query(CallRecord).order_by(CallRecord.id.desc()).all()
        for call in call_list:
            refresh_call_from_vapi(call, db)
            mark_stale_active_call_failed(call, db)
        return [serialize_call(call) for call in call_list]


@router.get("/calls/{call_id}")
def get_call(call_id: int):
    with SessionLocal() as db:
        call = db.query(CallRecord).filter(CallRecord.id == call_id).first()
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        refresh_call_from_vapi(call, db)
        mark_stale_active_call_failed(call, db)
        return serialize_call(call)


@router.post("/calls/{call_id}/end")
def end_call_endpoint(call_id: int):
    """End or cancel an active call."""
    from app.services.vapi import end_call
    
    with SessionLocal() as db:
        call = db.query(CallRecord).filter(CallRecord.id == call_id).first()
        if not call:
            raise HTTPException(status_code=404, detail="Call not found")
        
        # If call is queued or pending, try to end it via VAPI
        if call.external_id and call.status in ["Queued", "Pending", "In Progress"]:
            ret = end_call(str(call.external_id))
            if ret.get("ok"):
                call.status = "Ended"
            else:
                # Even if VAPI end fails, mark it as ended locally
                call.status = "Ended"
        else:
            call.status = "Ended"
        
        db.commit()
        db.refresh(call)
        return {"message": "Call ended", "data": serialize_call(call)}


def new_call_timestamp() -> str:
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %I:%M %p')
