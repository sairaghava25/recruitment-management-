"""Pydantic request and response schemas for the recruitment API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    ApplicationStatus,
    EmploymentType,
    ExceptionType,
    InterviewOutcome,
    InterviewStatus,
    InterviewType,
    OfferStatus,
    Recommendation,
    RequisitionStatus,
    ScreeningRuleType,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ScreeningRuleInput(BaseModel):
    skill_id: str | None = Field(default=None, max_length=20)
    rule_type: ScreeningRuleType
    criterion_name: str = Field(min_length=1, max_length=100)
    weight: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_proficiency: int | None = Field(default=None, ge=1, le=5)
    minimum_value: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_rule(self) -> ScreeningRuleInput:
        skill_rule = self.rule_type in {
            ScreeningRuleType.REQUIRED_SKILL,
            ScreeningRuleType.WEIGHTED_SKILL,
        }
        if skill_rule and self.skill_id is None:
            raise ValueError("skill screening rules require skill_id")
        if not skill_rule and self.skill_id is not None:
            raise ValueError("non-skill screening rules must not specify skill_id")
        if self.rule_type in {
            ScreeningRuleType.MIN_EXPERIENCE,
            ScreeningRuleType.PASS_THRESHOLD,
        } and self.minimum_value is None:
            raise ValueError("this screening rule requires minimum_value")
        if self.rule_type == ScreeningRuleType.PASS_THRESHOLD and self.weight != 0:
            raise ValueError("pass-threshold rules must have zero weight")
        if (
            self.rule_type == ScreeningRuleType.PASS_THRESHOLD
            and self.minimum_value is not None
            and self.minimum_value > 100
        ):
            raise ValueError("pass thresholds must not exceed 100")
        return self


class RequisitionCreate(BaseModel):
    department_id: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1)
    employment_type: EmploymentType
    location: str = Field(min_length=1, max_length=160)
    salary_min: Decimal = Field(ge=0)
    salary_max: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    headcount: int = Field(default=1, gt=0)
    created_by: str = Field(min_length=1, max_length=120)
    screening_rules: list[ScreeningRuleInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_salary_band(self) -> RequisitionCreate:
        if self.salary_max < self.salary_min:
            raise ValueError("salary_max must be greater than or equal to salary_min")
        if not any(
            rule.rule_type == ScreeningRuleType.REQUIRED_SKILL
            for rule in self.screening_rules
        ):
            raise ValueError("at least one required-skill screening rule is required")
        if sum(
            rule.weight
            for rule in self.screening_rules
            if rule.rule_type != ScreeningRuleType.PASS_THRESHOLD
        ) <= 0:
            raise ValueError("screening criteria must have a positive total weight")
        if sum(
            rule.rule_type == ScreeningRuleType.PASS_THRESHOLD
            for rule in self.screening_rules
        ) > 1:
            raise ValueError("only one pass-threshold rule is allowed")
        return self


class RequisitionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1)
    employment_type: EmploymentType | None = None
    location: str | None = Field(default=None, min_length=1, max_length=160)
    salary_min: Decimal | None = Field(default=None, ge=0)
    salary_max: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    headcount: int | None = Field(default=None, gt=0)
    updated_by: str = Field(default="api:recruiter", min_length=1, max_length=120)


class RequisitionRead(ORMModel):
    requisition_id: str
    department_id: str
    title: str
    description: str
    employment_type: EmploymentType
    location: str
    salary_min: Decimal
    salary_max: Decimal
    currency: str
    headcount: int
    status: RequisitionStatus
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime


class CandidateRead(ORMModel):
    candidate_id: str
    full_name: str
    email: str
    phone: str | None
    location: str | None
    professional_summary: str | None
    portfolio_url: str | None
    created_at: datetime


class ApplicationCreate(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=20)
    requisition_id: str = Field(min_length=1, max_length=20)
    source: str | None = Field(default=None, max_length=80)


class ApplicationRead(ORMModel):
    application_id: str
    requisition_id: str
    candidate_id: str
    status: ApplicationStatus
    submitted_at: datetime
    screening_score: Decimal | None
    screening_explanation: dict[str, Any] | None
    source: str | None
    updated_at: datetime


class ScreeningSkill(BaseModel):
    skill_id: str
    name: str
    proficiency_level: int | None
    required: bool


class ScreeningRead(BaseModel):
    application_id: str
    score: Decimal
    pass_threshold: Decimal
    required_skills_met: bool
    outcome: Literal["pass", "fail"]
    matched_skills: list[ScreeningSkill]
    missing_skills: list[ScreeningSkill]
    criteria: list[dict[str, Any]]


class InterviewCreate(BaseModel):
    application_id: str = Field(min_length=1, max_length=80)
    round_number: int = Field(gt=0)
    interview_type: InterviewType
    starts_at: datetime
    ends_at: datetime
    interviewer_ids: list[str] = Field(min_length=1)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("interview timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_schedule(self) -> InterviewCreate:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        if len(set(self.interviewer_ids)) != len(self.interviewer_ids):
            raise ValueError("interviewer_ids must not contain duplicates")
        return self


class InterviewScheduleUpdate(BaseModel):
    starts_at: datetime
    ends_at: datetime
    interviewer_ids: list[str] = Field(min_length=1)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("interview timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_schedule(self) -> InterviewScheduleUpdate:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        if len(set(self.interviewer_ids)) != len(self.interviewer_ids):
            raise ValueError("interviewer_ids must not contain duplicates")
        return self


class InterviewRead(ORMModel):
    interview_id: str
    application_id: str
    round_number: int
    interview_type: InterviewType
    starts_at: datetime
    ends_at: datetime
    status: InterviewStatus
    outcome: InterviewOutcome | None
    issue_type: ExceptionType | None
    interviewer_ids: list[str]


class FeedbackCreate(BaseModel):
    interviewer_id: str = Field(min_length=1, max_length=24)
    technical_rating: int = Field(ge=1, le=5)
    communication_rating: int = Field(ge=1, le=5)
    role_rating: int = Field(ge=1, le=5)
    recommendation: Recommendation
    comments: str | None = None


class FeedbackRead(ORMModel):
    feedback_id: str
    interview_id: str
    interviewer_id: str
    technical_rating: int
    communication_rating: int
    role_rating: int
    recommendation: Recommendation
    comments: str | None
    submitted_at: datetime


class AdvanceRequest(BaseModel):
    to_status: ApplicationStatus
    actor: str = Field(min_length=1, max_length=120)
    reason: str | None = None


class OfferCreate(BaseModel):
    salary: Decimal = Field(gt=0)
    created_by: str = Field(min_length=1, max_length=120)


class OfferAction(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
    reason: str | None = None


class OfferResponseRequest(BaseModel):
    decision: Literal["accepted", "rejected"]
    actor: str = Field(min_length=1, max_length=120)
    reason: str | None = None

    @model_validator(mode="after")
    def require_rejection_reason(self) -> OfferResponseRequest:
        if self.decision == "rejected" and not (self.reason and self.reason.strip()):
            raise ValueError("a rejection reason is required")
        return self


class OfferRead(ORMModel):
    offer_id: str
    application_id: str
    salary: Decimal
    currency: str
    status: OfferStatus
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    issued_at: datetime | None
    responded_at: datetime | None


class AuditRead(ORMModel):
    audit_event_id: str
    entity_type: str
    entity_id: str
    action: str
    actor: str
    changes: dict[str, Any] | None
    occurred_at: datetime


class ExceptionRead(ORMModel):
    exception_id: str
    issue_type: ExceptionType
    entity_type: str
    entity_id: str
    expected_decision: str
    observed_details: dict[str, Any]
    validation_result: str
    detected_at: datetime
    severity: str
    detected: bool


class PipelineStage(BaseModel):
    stage: ApplicationStatus
    count: int


class ApplicationTrend(BaseModel):
    month: str
    applications: int


class ExceptionGroup(BaseModel):
    severity: str
    issue_type: str
    count: int


class DashboardSummary(BaseModel):
    total_requisitions: int
    open_requisitions: int
    candidates: int
    applications: int
    interviews: int
    offers: int
    accepted_offers: int
    rejected_offers: int
    pending_offers: int
    pipeline: list[PipelineStage]
    scored_applications: int
    average_screening_score: float | None
    recorded_passes: int
    recorded_fails: int
    application_trend: list[ApplicationTrend]
