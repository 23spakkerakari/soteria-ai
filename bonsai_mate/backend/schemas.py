"""Data shapes for the two separate layers:

- Scenario: a situation used to TEST the assistant (the benchmark).
- Card: what the assistant is allowed to RECOMMEND (the clinical knowledge base).

Both carry source attribution and a clinical-review status on every record.
"""
from typing import Literal

from pydantic import BaseModel, Field

ReviewStatus = Literal["pending", "approved", "rejected"]
SourceType = Literal[
    "educational_case",
    "simulation_case",
    "first_aid_training",
    "academic_simulation",
    "guideline",
    "synthetic",
]


class Source(BaseModel):
    organization: str
    source_type: SourceType
    source_url: str | None = None
    license_verified: bool = False


class Environment(BaseModel):
    type: str = "wilderness"
    weather: str = "unknown"
    connectivity: str = "none"
    distance_to_help: str = "unknown"


class Patient(BaseModel):
    age: int | None = None
    sex: str = "unknown"
    medical_history: str = "unknown"


class Presentation(BaseModel):
    chief_complaint: str
    symptoms: list[str] = []
    # Other ways a real person might open the conversation. Used to make variations.
    lay_descriptions: list[str] = []


class Turn(BaseModel):
    question: str
    answer: str


class Evaluation(BaseModel):
    """Expected behaviour. Each item is a set of alternatives separated by "|";
    a check passes if any alternative appears in the reply. These are NOT
    validated clinical answers until clinical_review is "approved"."""

    must_recognize: list[str] = []
    must_ask: list[str] = []
    must_escalate: list[str] = []
    must_not_recommend: list[str] = []
    clinical_review: ReviewStatus = "pending"


class Scenario(BaseModel):
    scenario_id: str = Field(pattern=r"^[A-Z]+_\d{4}(_V\d{2})?$")
    title: str
    variation_of: str | None = None
    tags: list[str] = []
    source: Source
    environment: Environment = Environment()
    patient: Patient = Patient()
    presentation: Presentation
    conversation: list[Turn] = []
    evaluation: Evaluation = Evaluation()

    def opening_message(self) -> str:
        """What the user would type to the assistant."""
        return " ".join(t.answer for t in self.conversation) or self.presentation.chief_complaint


class Card(BaseModel):
    card_id: str = Field(pattern=r"^KB_[A-Z_]+$")
    title: str
    keywords: list[str]  # include the words scared people actually use
    recognize: list[str]
    do_first: list[str]
    do_not: list[str] = []
    escalate_when: list[str] = []
    source: Source
    clinical_review: ReviewStatus = "pending"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    thinking: bool = False
    # One id per incident, made by the page. The question-and-answer record is kept under it.
    incident_id: str | None = Field(default=None, max_length=64)
    # The user tapped "Give me the plan now" instead of answering the questions.
    plan_now: bool = False
