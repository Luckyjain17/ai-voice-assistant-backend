import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request

from app.schemas import CallCreate
from app.services.vapi import get_call_details, initiate_call

router = APIRouter(prefix="/api")
TEMP_CALLS: dict[int, dict[str, Any]] = {}
TEMP_CALLS_BY_EXTERNAL_ID: dict[str, int] = {}

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


def serialize_call(call: dict[str, Any]) -> dict:
    questions = call.get("questions", "").split("||") if call.get("questions") else []
    responses = call.get("responses", "").split("||") if call.get("responses") else []
    
    # Parse and clean transcript
    transcript_lines = split_transcript_lines(call.get("transcript")) if call.get("transcript") else []
    
    # Extract responses if not already available
    if not any(r.strip() for r in responses) and transcript_lines and questions:
        responses = extract_responses_from_transcript(questions, call.get("transcript"))

    return {
        "id": call.get("id"),
        "name": call.get("name", ""),
        "phone": call.get("phone", ""),
        "status": call.get("status", "Pending"),
        "duration": call.get("duration") or "Pending",
        "externalId": call.get("external_id"),
        "questionsAsked": questions,
        "responses": responses,
        "transcript": transcript_lines,
        "createdAt": call.get("created_at") or "",
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


def refresh_call_from_vapi(call: dict[str, Any]) -> None:
    if not call.get("external_id") or call.get("transcript"):
        return

    ret = get_call_details(str(call.get("external_id")))
    if not ret.get("ok"):
        return

    body = ret.get("body") or {}
    transcript = extract_vapi_transcript(body)
    if not transcript:
        return

    call["transcript"] = transcript
    questions = call.get("questions", "").split("||") if call.get("questions") else FIXED_CALL_QUESTIONS
    responses = extract_responses_from_transcript(questions, call["transcript"])
    if any(responses):
        call["responses"] = "||".join(responses)

    status = body.get("status")
    if status:
        call["status"] = normalize_status(str(status))

    duration_seconds = body.get("durationSeconds") or body.get("duration")
    if duration_seconds:
        call["duration"] = str(duration_seconds)


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

    temp_id = TEMP_CALLS_BY_EXTERNAL_ID.get(str(call_id))
    call = TEMP_CALLS.get(temp_id) if temp_id else None

    if call:
        if transcript:
            if call.get("transcript"):
                existing_lines = set(call["transcript"].split("||"))
                new_lines = transcript.split("||") if isinstance(transcript, str) else [transcript]
                for line in new_lines:
                    if line.strip() and line.strip() not in existing_lines:
                        call["transcript"] = f"{call['transcript']}||{line}"
            else:
                call["transcript"] = transcript

            questions = call.get("questions", "").split("||") if call.get("questions") else FIXED_CALL_QUESTIONS
            responses = extract_responses_from_transcript(questions, call.get("transcript"))
            if any(r.strip() for r in responses):
                call["responses"] = "||".join(responses)

        status = message.get("status") or data.get("status")
        if status and isinstance(status, str):
            call["status"] = normalize_status(status)

    # Temporary DB-free mode: accept Vapi webhooks even when no persisted call exists.
    return {"ok": True, "id": temp_id, "stored": bool(call)}


@router.post("/start-call")
def start_call(data: CallCreate):
    temp_id = int(datetime.now().timestamp() * 1000)
    new_call = {
        "id": temp_id,
        "name": data.name,
        "phone": data.phone,
        "status": "Pending",
        "duration": "Pending",
        "questions": "||".join(FIXED_CALL_QUESTIONS),
        "responses": "",
        "transcript": "",
        "created_at": new_call_timestamp(),
        "external_id": None,
    }

    try:
        ret = initiate_call(phone=new_call["phone"], name=new_call["name"])
        if ret.get("ok"):
            body = ret.get("body") or {}
            ext_id = body.get("id") or body.get("call_id") or body.get("external_id")
            new_call["external_id"] = ext_id
            new_call["status"] = "Queued"
            if ext_id:
                TEMP_CALLS_BY_EXTERNAL_ID[str(ext_id)] = temp_id
        else:
            err = ret.get("error")
            new_call["status"] = "FailedToQueue"
            new_call["responses"] = f"vapi_error:{err}"
    except Exception as exc:
        new_call["status"] = "FailedToQueue"
        new_call["responses"] = f"vapi_exception:{str(exc)}"

    TEMP_CALLS[temp_id] = new_call
    message = "Call queued" if new_call["status"] == "Queued" else "Call failed to queue"
    return {"message": message, "data": serialize_call(new_call)}


@router.get("/calls")
def get_calls():
    # Temporary DB-free mode for Railway deployment.
    return []


@router.get("/calls/{call_id}")
def get_call(call_id: int):
    call = TEMP_CALLS.get(call_id)
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

    refresh_call_from_vapi(call)
    return serialize_call(call)


@router.post("/calls/{call_id}/end")
def end_call_endpoint(call_id: int):
    """End or cancel an active call."""
    from app.services.vapi import end_call

    call = TEMP_CALLS.get(call_id)
    if not call:
        call = {
            "id": call_id,
            "name": "Temporary call",
            "phone": "",
            "status": "Ended",
            "duration": "Pending",
            "questions": "||".join(FIXED_CALL_QUESTIONS),
            "responses": "",
            "transcript": "",
            "created_at": new_call_timestamp(),
            "external_id": None,
        }
        TEMP_CALLS[call_id] = call

    if call.get("external_id") and call.get("status") in ["Queued", "Pending", "In Progress"]:
        end_call(str(call["external_id"]))

    call["status"] = "Ended"
    return {"message": "Call ended", "data": serialize_call(call)}


def new_call_timestamp() -> str:
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %I:%M %p')
