from typing import List, Optional

question_templates = [
    {
        "id": "discovery",
        "label": "Product Discovery",
        "questions": ["What brought you here today?", "What feature matters most?"],
        "responses": ["I want to optimize my workflow", "Ease of use and reliability"],
    },
    {
        "id": "feedback",
        "label": "Customer Feedback",
        "questions": ["How was your experience?", "What should improve?"],
        "responses": ["The call was smooth", "More natural prompts would help"],
    },
    {
        "id": "demo",
        "label": "Demo Scheduling",
        "questions": ["When can we schedule a demo?", "Which time zone are you in?"],
        "responses": ["Tomorrow after 2PM", "Asia/Kolkata"],
    },
]


def get_template(template_id: str) -> dict:
    return next((template for template in question_templates if template["id"] == template_id), question_templates[0])


def build_transcript(questions: List[str], responses: List[str]) -> List[str]:
    return [item for pair in zip([f"AI: {question}" for question in questions], [f"User: {response}" for response in responses]) for item in pair]
