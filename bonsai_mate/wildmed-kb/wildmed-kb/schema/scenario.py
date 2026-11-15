"""
Normalized scenario schema for an offline wilderness / travel emergency assistant.

One ScenarioRecord = one sourced case (educational case study, simulation case,
or peer-reviewed simulation). Records are retrieval + evaluation objects, NOT
validated treatment protocols. Everything under `source_management` is what the
SOURCE said, paraphrased, and stays `clinical_review: pending` until a clinician
signs off.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------- vocab
# Controlled vocabularies. Free text is allowed in narrative fields only.
SETTINGS = Literal[
    "wilderness", "backcountry_water", "frontcountry", "campground",
    "road", "aircraft", "urban", "prehospital_ems", "hospital_ed", "classroom_sim",
]
CATEGORY = Literal["injury", "environmental", "medical", "mixed"]
ACUITY = Literal[
    "life_threat",      # immediate threat to life; activate EMS / rapid evac now
    "urgent",           # needs evacuation / definitive care within hours
    "semi_urgent",      # evacuate or see a clinician within ~24-48h
    "field_manageable", # source managed in the field with monitoring
]
EVAC_MODE = Literal[
    "none", "self_evac_walk", "assisted_walk", "litter_carry", "vehicle_private",
    "ems_ground", "helicopter", "sar_team", "unknown",
]
AGE_GROUP = Literal["infant", "child", "adolescent", "adult", "older_adult", "unknown"]
REUSE = Literal[
    "redistributable_attribution",        # e.g. CC BY / CC BY-SA
    "noncommercial_only",                 # e.g. CC BY-NC
    "internal_reference_only",            # all rights reserved: facts paraphrased, no verbatim text, verify before shipping
]

# Condition tags. Keep snake_case, add new ones here (validator enforces).
CONDITION_TAGS = {
    # trauma
    "spine_injury_possible", "spinal_cord_injury", "head_injury_mild", "head_injury_serious",
    "concussion", "shock_possible", "hypovolemic_shock", "acute_stress_reaction",
    "internal_injury_possible", "fracture_closed", "fracture_open", "dislocation",
    "partial_amputation", "severe_hemorrhage", "laceration", "abrasion", "contusion",
    "sprain_strain", "ankle_injury", "shoulder_injury", "chest_trauma", "pneumothorax",
    "multisystem_trauma", "burn", "rib_fracture", "wrist_injury", "knee_injury",
    "dental_injury", "eye_injury", "chemical_exposure",
    # environmental
    "hypothermia_mild", "hypothermia_moderate_severe", "frostbite", "frostnip",
    "nonfreezing_cold_injury", "heat_exhaustion", "heat_stroke", "heat_cramps",
    "exercise_associated_hyponatremia", "dehydration", "lightning_injury",
    "acute_mountain_sickness", "hace", "hape", "drowning", "envenomation_snake",
    "envenomation_insect", "envenomation_marine", "bear_encounter", "cold_water_immersion",
    "motion_sickness", "smoke_exposure",
    # medical
    "cardiac_arrest", "acute_coronary_syndrome", "chest_pain", "arrhythmia",
    "anxiety_panic", "hyperventilation", "anaphylaxis", "allergic_reaction",
    "soft_tissue_infection", "sepsis_possible", "fever", "rash", "contact_dermatitis",
    "tick_borne_illness", "viral_illness", "gastroenteritis", "abdominal_pain",
    "appendicitis_possible", "seizure", "altered_mental_status", "diabetic_emergency",
    "asthma", "respiratory_distress", "pregnancy_complication", "toxic_exposure",
}


class Source(BaseModel):
    organization: str
    source_type: Literal[
        "educational_case_study", "simulation_case", "peer_reviewed_simulation",
        "first_aid_course_material", "clinical_case_report",
    ]
    title: str
    url: str
    published: Optional[str] = None          # YYYY or YYYY-MM or YYYY-MM-DD
    authors: list[str] = []
    license: str                              # SPDX-ish string or "All rights reserved"
    reuse_status: REUSE
    based_on_real_event: Optional[bool] = None
    retrieved: str                            # YYYY-MM-DD


class Classification(BaseModel):
    category: CATEGORY
    conditions: list[str]                     # CONDITION_TAGS
    primary_condition: str
    mechanism: Optional[str] = None           # e.g. "fall_from_horse", "cold_exposure"
    acuity: ACUITY                            # derived from source's disposition
    acuity_basis: str                         # why we tagged it that way

    @field_validator("conditions")
    @classmethod
    def _known(cls, v):
        bad = [c for c in v if c not in CONDITION_TAGS]
        if bad:
            raise ValueError(f"unknown condition tags: {bad}")
        return v

    @model_validator(mode="after")
    def _primary_in_list(self):
        if self.primary_condition not in self.conditions:
            raise ValueError("primary_condition must also appear in conditions")
        return self


class Context(BaseModel):
    setting: SETTINGS
    activity: Optional[str] = None            # hiking, climbing, kayaking ...
    region: Optional[str] = None
    terrain: Optional[str] = None
    weather: Optional[str] = None
    ambient_temp_c: Optional[float] = None
    altitude_m: Optional[float] = None
    connectivity: Literal["none", "limited", "available", "unknown"] = "unknown"
    distance_to_care: Optional[str] = None    # free text from source
    responder_training: Optional[str] = None  # "WFR", "lay", "SAR", "EMS", "physician_trainee"
    group: Optional[str] = None
    resources_available: list[str] = []


class Patient(BaseModel):
    age_years: Optional[float] = None
    age_group: AGE_GROUP
    sex: Literal["male", "female", "unspecified"] = "unspecified"
    pertinent_history: list[str] = []
    medications: list[str] = []
    allergies: list[str] = []


class VitalSet(BaseModel):
    time: Optional[str] = None                # as given by source, e.g. "15:30", "Day 2 08:00"
    position: Optional[str] = None            # supine/standing etc. if stated
    loc: Optional[str] = None                 # AVPU / A+Ox#
    hr: Optional[int] = None
    hr_quality: Optional[str] = None
    rr: Optional[int] = None
    rr_quality: Optional[str] = None
    skin: Optional[str] = None                # SCTM
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    bp_note: Optional[str] = None             # "radial pulse present"
    pupils: Optional[str] = None
    temp_c: Optional[float] = None
    spo2: Optional[int] = None
    pain_0_10: Optional[int] = None
    note: Optional[str] = None


class Presentation(BaseModel):
    summary: str                              # 2-4 sentence paraphrase of the scene
    chief_complaint: str
    symptoms: list[str] = []
    exam_findings: list[str] = []
    pertinent_negatives: list[str] = []
    last_intake_output: Optional[str] = None
    events_leading_up: Optional[str] = None


class DecisionPoint(BaseModel):
    question: str                             # "Is spine protection still needed?"
    source_decision: str                      # what the source decided
    basis: list[str] = []                     # findings the decision rested on


class SourceManagement(BaseModel):
    """What the source says. Unvalidated. Never served as a recommendation without review."""
    assessment: list[str]
    plan: list[str]
    anticipated_problems: list[str] = []
    teaching_points: list[str] = []
    decision_points: list[DecisionPoint] = []


class Outcome(BaseModel):
    summary: Optional[str] = None
    evacuation_mode: EVAC_MODE = "unknown"
    final_diagnosis: Optional[str] = None


class EvalSpec(BaseModel):
    """Seed expectations for benchmarking the assistant. Derived from the source; needs review."""
    must_recognize: list[str] = []
    must_ask: list[str] = []
    must_escalate: Optional[str] = None       # plain statement of escalation expected
    must_not_recommend: list[str] = []
    derived_from: Literal["source_answer_key", "source_learning_objectives", "editor"] = "source_answer_key"
    clinical_review: Literal["pending", "approved", "rejected"] = "pending"


class Retrieval(BaseModel):
    keywords: list[str]
    lay_queries: list[str]                    # how a panicked non-clinician might describe it
    red_flags: list[str] = []                 # findings in this case that should trigger escalation


class Provenance(BaseModel):
    extraction_method: str = "llm_assisted_manual"
    extracted_on: str
    verbatim_text_stored: bool = False
    notes: Optional[str] = None


class ScenarioRecord(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    schema_version: str = SCHEMA_VERSION
    title: str
    language: str = "en"
    source: Source
    classification: Classification
    context: Context
    patient: Patient
    presentation: Presentation
    vitals: list[VitalSet] = []
    source_management: SourceManagement
    outcome: Outcome
    eval: EvalSpec
    retrieval: Retrieval
    provenance: Provenance
