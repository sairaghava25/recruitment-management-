from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class RequisitionStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    OPEN = "open"
    ON_HOLD = "on_hold"
    CLOSED = "closed"


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"


class ApplicationStatus(str, Enum):
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    HIRED = "hired"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ResumeStatus(str, Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class ScreeningRuleType(str, Enum):
    REQUIRED_SKILL = "required_skill"
    WEIGHTED_SKILL = "weighted_skill"
    MIN_EXPERIENCE = "min_experience"
    PASS_THRESHOLD = "pass_threshold"


class InterviewStatus(str, Enum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class InterviewType(str, Enum):
    PHONE = "phone"
    VIDEO = "video"
    ONSITE = "onsite"
    TECHNICAL = "technical"
    PANEL = "panel"


class InterviewOutcome(str, Enum):
    ADVANCE = "advance"
    REJECT = "reject"
    HOLD = "hold"


class Recommendation(str, Enum):
    STRONG_YES = "strong_yes"
    YES = "yes"
    NO = "no"
    STRONG_NO = "strong_no"


class OfferStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ISSUED = "issued"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ExceptionType(str, Enum):
    UNAVAILABLE_INTERVIEWER = "unavailable_interviewer"
    DUPLICATE_APPLICATION = "duplicate_application"
    MISSING_FEEDBACK_ADVANCEMENT = "missing_feedback_advancement"
    OFFER_OUTSIDE_SALARY_BAND = "offer_outside_salary_band"
    CLOSED_REQUISITION_ACTIVE_APPLICATIONS = "closed_requisition_active_applications"


def enum_values(enum_type: type[Enum]) -> list[str]:
    return [member.value for member in enum_type]


def enum_column(enum_type: type, name: str) -> SqlEnum:
    return SqlEnum(
        enum_type,
        name=name,
        values_callable=enum_values,
        native_enum=True,
        validate_strings=True,
    )


class Department(Base):
    __tablename__ = "departments"

    department_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobRequisition(Base):
    __tablename__ = "job_requisitions"
    __table_args__ = (
        CheckConstraint("salary_min >= 0 AND salary_max >= salary_min", name="ck_requisition_salary_band"),
        CheckConstraint("headcount > 0", name="ck_requisition_headcount_positive"),
        Index("ix_requisition_department_status", "department_id", "status"),
    )

    requisition_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.department_id"), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    employment_type: Mapped[EmploymentType] = mapped_column(enum_column(EmploymentType, "employment_type"))
    location: Mapped[str] = mapped_column(String(160), nullable=False)
    salary_min: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    salary_max: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    headcount: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[RequisitionStatus] = mapped_column(
        enum_column(RequisitionStatus, "requisition_status"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(120))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Candidate(Base):
    __tablename__ = "candidates"

    candidate_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    location: Mapped[str | None] = mapped_column(String(160))
    professional_summary: Mapped[str | None] = mapped_column(Text)
    portfolio_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Skill(Base):
    __tablename__ = "skills"

    skill_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CandidateSkill(Base):
    __tablename__ = "candidate_skills"
    __table_args__ = (
        CheckConstraint("proficiency_level BETWEEN 1 AND 5", name="ck_candidate_skill_proficiency"),
        CheckConstraint("years_experience >= 0", name="ck_candidate_skill_years_nonnegative"),
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_candidate_skill_dates"),
    )

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.candidate_id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"), primary_key=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    proficiency_level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    years_experience: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResumeArtifact(Base):
    __tablename__ = "resume_artifacts"
    __table_args__ = (Index("ix_resume_candidate_uploaded", "candidate_id", "uploaded_at"),)

    resume_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.candidate_id", ondelete="CASCADE"))
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.application_id"))
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_status: Mapped[ResumeStatus] = mapped_column(
        enum_column(ResumeStatus, "resume_status"), nullable=False
    )
    extracted_text: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("requisition_id", "candidate_id", name="uq_application_requisition_candidate"),
        CheckConstraint("screening_score IS NULL OR screening_score BETWEEN 0 AND 100", name="ck_application_score"),
        Index("ix_application_requisition_status", "requisition_id", "status"),
        Index("ix_application_candidate", "candidate_id"),
    )

    application_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    requisition_id: Mapped[str] = mapped_column(ForeignKey("job_requisitions.requisition_id"), nullable=False)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.candidate_id"), nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        enum_column(ApplicationStatus, "application_status"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    screening_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    screening_explanation: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScreeningRule(Base):
    __tablename__ = "screening_rules"
    __table_args__ = (
        CheckConstraint("weight >= 0", name="ck_screening_rule_weight_nonnegative"),
        CheckConstraint("minimum_proficiency IS NULL OR minimum_proficiency BETWEEN 1 AND 5", name="ck_screening_rule_proficiency"),
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_screening_rule_dates"),
        Index("ix_screening_rule_requisition_active", "requisition_id", "is_active"),
    )

    screening_rule_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    requisition_id: Mapped[str] = mapped_column(
        ForeignKey("job_requisitions.requisition_id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[str | None] = mapped_column(ForeignKey("skills.skill_id"))
    rule_type: Mapped[ScreeningRuleType] = mapped_column(
        enum_column(ScreeningRuleType, "screening_rule_type"), nullable=False
    )
    criterion_name: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    minimum_proficiency: Mapped[int | None] = mapped_column(SmallInteger)
    minimum_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Interviewer(Base):
    __tablename__ = "interviewers"

    interviewer_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.department_id"))
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InterviewerAvailability(Base):
    __tablename__ = "interviewer_availability"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_availability_dates"),
        Index("ix_availability_interviewer_time", "interviewer_id", "starts_at", "ends_at"),
    )

    availability_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    interviewer_id: Mapped[str] = mapped_column(
        ForeignKey("interviewers.interviewer_id", ondelete="CASCADE"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


interview_panel = Table(
    "interview_panel",
    Base.metadata,
    Column("interview_id", String(24), ForeignKey("interviews.interview_id", ondelete="CASCADE"), primary_key=True),
    Column("interviewer_id", String(24), ForeignKey("interviewers.interviewer_id"), primary_key=True),
)


class Interview(Base):
    __tablename__ = "interviews"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_interview_dates"),
        CheckConstraint("round_number > 0", name="ck_interview_round_positive"),
        UniqueConstraint("application_id", "round_number", name="uq_interview_application_round"),
        Index("ix_interview_starts_status", "starts_at", "status"),
    )

    interview_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.application_id"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    interview_type: Mapped[InterviewType] = mapped_column(enum_column(InterviewType, "interview_type"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[InterviewStatus] = mapped_column(
        enum_column(InterviewStatus, "interview_status"), nullable=False
    )
    outcome: Mapped[InterviewOutcome | None] = mapped_column(enum_column(InterviewOutcome, "interview_outcome"))
    issue_type: Mapped[ExceptionType | None] = mapped_column(
        enum_column(ExceptionType, "interview_issue_type")
    )


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["interview_id", "interviewer_id"],
            ["interview_panel.interview_id", "interview_panel.interviewer_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("interview_id", "interviewer_id", name="uq_feedback_panelist"),
        CheckConstraint("technical_rating BETWEEN 1 AND 5", name="ck_feedback_technical_rating"),
        CheckConstraint("communication_rating BETWEEN 1 AND 5", name="ck_feedback_communication_rating"),
        CheckConstraint("role_rating BETWEEN 1 AND 5", name="ck_feedback_role_rating"),
    )

    feedback_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    interview_id: Mapped[str] = mapped_column(String(24), nullable=False)
    interviewer_id: Mapped[str] = mapped_column(String(24), nullable=False)
    technical_rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    communication_rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    role_rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    recommendation: Mapped[Recommendation] = mapped_column(
        enum_column(Recommendation, "recommendation"), nullable=False
    )
    comments: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint("salary > 0", name="ck_offer_salary_positive"),
        Index("ix_offer_application_status", "application_id", "status"),
    )

    offer_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.application_id"), nullable=False)
    salary: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[OfferStatus] = mapped_column(enum_column(OfferStatus, "offer_status"), nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(120))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RequisitionEvent(Base):
    __tablename__ = "requisition_events"

    event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    requisition_id: Mapped[str] = mapped_column(
        ForeignKey("job_requisitions.requisition_id"), nullable=False
    )
    from_status: Mapped[RequisitionStatus | None] = mapped_column(
        enum_column(RequisitionStatus, "requisition_event_from_status")
    )
    to_status: Mapped[RequisitionStatus] = mapped_column(
        enum_column(RequisitionStatus, "requisition_event_to_status"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.application_id"), nullable=False
    )
    from_status: Mapped[ApplicationStatus | None] = mapped_column(
        enum_column(ApplicationStatus, "application_event_from_status")
    )
    to_status: Mapped[ApplicationStatus] = mapped_column(
        enum_column(ApplicationStatus, "application_event_to_status"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DuplicateApplicationAttempt(Base):
    __tablename__ = "duplicate_application_attempts"

    attempt_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    requisition_id: Mapped[str] = mapped_column(
        ForeignKey("job_requisitions.requisition_id"), nullable=False
    )
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.candidate_id"), nullable=False)
    original_application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.application_id"), nullable=False
    )
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issue_type: Mapped[ExceptionType] = mapped_column(
        enum_column(ExceptionType, "duplicate_attempt_issue_type"), nullable=False
    )


class OfferEvent(Base):
    __tablename__ = "offer_events"

    event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    offer_id: Mapped[str] = mapped_column(ForeignKey("offers.offer_id"), nullable=False)
    from_status: Mapped[OfferStatus | None] = mapped_column(enum_column(OfferStatus, "offer_event_from_status"))
    to_status: Mapped[OfferStatus] = mapped_column(
        enum_column(OfferStatus, "offer_event_to_status"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity_time", "entity_type", "entity_id", "occurred_at"),)

    audit_event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    changes: Mapped[dict | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExceptionRecord(Base):
    __tablename__ = "exception_records"
    __table_args__ = (
        Index("ix_exception_type_detected", "issue_type", "detected_at"),
        Index("ix_exception_source", "entity_type", "entity_id"),
    )

    exception_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    issue_type: Mapped[ExceptionType] = mapped_column(
        enum_column(ExceptionType, "exception_type"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    expected_decision: Mapped[str] = mapped_column(String(80), nullable=False)
    observed_details: Mapped[dict] = mapped_column(JSON, nullable=False)
    validation_result: Mapped[str] = mapped_column(String(40), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)