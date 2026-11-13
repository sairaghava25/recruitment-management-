"""FastAPI application and database-backed recruitment API routes."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Iterator
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import and_, case, delete, extract, func, insert, select, true
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from . import models
from .database import get_session_factory
from .models import (
    ApplicationStatus,
    ExceptionType,
    InterviewOutcome,
    InterviewStatus,
    OfferStatus,
    Recommendation,
    RequisitionStatus,
    ScreeningRuleType,
)
from .schemas import (
    AdvanceRequest,
    ApplicationTrend,
    ApplicationCreate,
    ApplicationRead,
    AuditRead,
    CandidateRead,
    DashboardSummary,
    ExceptionGroup,
    ExceptionRead,
    FeedbackCreate,
    FeedbackRead,
    InterviewCreate,
    InterviewRead,
    InterviewScheduleUpdate,
    OfferAction,
    OfferCreate,
    OfferRead,
    OfferResponseRequest,
    PipelineStage,
    RequisitionCreate,
    RequisitionPatch,
    RequisitionRead,
    ScreeningRead,
    ScreeningSkill,
)
from .services import (
    ScreeningScore,
    calculate_screening_score,
    is_application_transition_valid,
    is_lifecycle_transition_valid,
)
from .data_tools import SEVERITY_BY_ISSUE, validate_dataset


router = APIRouter()


class DomainError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def get_db(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    if factory is None:
        if not os.getenv("DATABASE_URL"):
            raise DomainError(
                503,
                "database_not_configured",
                "Set DATABASE_URL before using database-backed endpoints.",
            )
        factory = get_session_factory()
        request.app.state.session_factory = factory
    session = factory()
    try:
        yield session
    finally:
        if session.in_transaction():
            session.rollback()
        session.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _next_yearly_id(session: Session, column: Any, prefix: str, year: int | None = None) -> str:
    year = year or _now().year
    stem = f"{prefix}-{year}-"
    values = session.scalars(select(column).where(column.like(f"{stem}%"))).all()
    suffixes = [int(value[len(stem) :]) for value in values if value[len(stem) :].isdigit()]
    return f"{stem}{max(suffixes, default=0) + 1:06d}"


def _next_numbered_id(session: Session, column: Any, prefix: str) -> str:
    values = session.scalars(select(column).where(column.like(f"{prefix}%"))).all()
    suffixes = [int(value[len(prefix) :]) for value in values if value[len(prefix) :].isdigit()]
    return f"{prefix}{max(suffixes, default=0) + 1:06d}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _audit(
    session: Session,
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str,
    changes: dict[str, Any] | None = None,
) -> None:
    session.add(
        models.AuditEvent(
            audit_event_id=uuid4().hex,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            changes=changes,
            occurred_at=_now(),
        )
    )


def _record_requisition_event(
    session: Session,
    requisition: models.JobRequisition,
    previous: RequisitionStatus | None,
    current: RequisitionStatus,
    actor: str,
    reason: str | None = None,
    occurred_at: datetime | None = None,
) -> None:
    if not is_lifecycle_transition_valid("requisition", previous, current):
        raise DomainError(409, "invalid_transition", "Invalid requisition status transition.")
    session.add(
        models.RequisitionEvent(
            event_id=uuid4().hex,
            requisition_id=requisition.requisition_id,
            from_status=previous,
            to_status=current,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at or _now(),
        )
    )
    requisition.status = current


def _record_application_event(
    session: Session,
    application: models.Application,
    current: ApplicationStatus,
    actor: str,
    reason: str | None = None,
) -> None:
    previous = application.status
    if not is_application_transition_valid(previous, current):
        raise DomainError(
            409,
            "invalid_transition",
            f"Application cannot transition from {_enum_value(previous)} to {_enum_value(current)}.",
        )
    session.add(
        models.ApplicationEvent(
            event_id=uuid4().hex,
            application_id=application.application_id,
            from_status=previous,
            to_status=current,
            actor=actor,
            reason=reason,
            occurred_at=_now(),
        )
    )
    application.status = current
    application.updated_at = _now()


def _record_offer_event(
    session: Session,
    offer: models.Offer,
    current: OfferStatus,
    actor: str,
    reason: str | None = None,
) -> None:
    previous = offer.status
    if not is_lifecycle_transition_valid("offer", previous, current):
        raise DomainError(
            409,
            "invalid_transition",
            f"Offer cannot transition from {_enum_value(previous)} to {_enum_value(current)}.",
        )
    session.add(
        models.OfferEvent(
            event_id=uuid4().hex,
            offer_id=offer.offer_id,
            from_status=previous,
            to_status=current,
            actor=actor,
            reason=reason,
            occurred_at=_now(),
        )
    )
    offer.status = current


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DomainError(
            409,
            "database_conflict",
            "The request conflicts with an existing record or database constraint.",
        ) from exc


def _require(
    session: Session, model: type[Any], key: str, value: str, entity_name: str
) -> Any:
    entity = session.get(model, value)
    if entity is None:
        raise DomainError(
            404,
            f"{entity_name}_not_found",
            f"{entity_name} was not found.",
            {"field": key, "value": value},
        )
    return entity


def _screening_data(
    session: Session, application: models.Application
) -> tuple[ScreeningScore, list[models.ScreeningRule], dict[str, tuple[int, Decimal]]]:
    now = _now()
    rules = session.scalars(
        select(models.ScreeningRule)
        .where(
            models.ScreeningRule.requisition_id == application.requisition_id,
            models.ScreeningRule.is_active.is_(True),
            models.ScreeningRule.effective_from <= now,
            (models.ScreeningRule.effective_to.is_(None))
            | (models.ScreeningRule.effective_to > now),
        )
        .order_by(models.ScreeningRule.screening_rule_id)
    ).all()
    if not rules:
        raise DomainError(409, "screening_rules_missing", "No active screening rules exist.")
    skills = session.scalars(
        select(models.CandidateSkill)
        .where(
            models.CandidateSkill.candidate_id == application.candidate_id,
            models.CandidateSkill.effective_from <= now,
            (models.CandidateSkill.effective_to.is_(None))
            | (models.CandidateSkill.effective_to > now),
        )
        .order_by(models.CandidateSkill.effective_from.desc())
    ).all()
    candidate_skills: dict[str, tuple[int, Decimal]] = {}
    for skill in skills:
        candidate_skills.setdefault(
            skill.skill_id, (skill.proficiency_level, Decimal(skill.years_experience))
        )
    return calculate_screening_score(candidate_skills, rules), rules, candidate_skills


def _screening_response(
    session: Session,
    application: models.Application,
    screening_data: tuple[
        ScreeningScore, list[models.ScreeningRule], dict[str, tuple[int, Decimal]]
    ]
    | None = None,
) -> ScreeningRead:
    result, rules, candidate_skills = screening_data or _screening_data(session, application)
    skill_ids = {
        rule.skill_id
        for rule in rules
        if rule.skill_id is not None
        and rule.rule_type
        in {ScreeningRuleType.REQUIRED_SKILL, ScreeningRuleType.WEIGHTED_SKILL}
    }
    skill_rows = session.scalars(
        select(models.Skill).where(models.Skill.skill_id.in_(skill_ids))
    ).all()
    names = {skill.skill_id: skill.name for skill in skill_rows}
    met_by_skill: dict[str, bool] = {}
    required_by_skill: dict[str, bool] = {}
    for rule in rules:
        if rule.skill_id is None or rule.rule_type not in {
            ScreeningRuleType.REQUIRED_SKILL,
            ScreeningRuleType.WEIGHTED_SKILL,
        }:
            continue
        minimum = rule.minimum_proficiency or 1
        proficiency = candidate_skills.get(rule.skill_id, (0, Decimal("0")))[0]
        met_by_skill[rule.skill_id] = (
            met_by_skill.get(rule.skill_id, True) and proficiency >= minimum
        )
        required_by_skill[rule.skill_id] = (
            required_by_skill.get(rule.skill_id, False)
            or rule.rule_type == ScreeningRuleType.REQUIRED_SKILL
        )

    def skill_result(skill_id: str) -> ScreeningSkill:
        proficiency = candidate_skills.get(skill_id, (None, Decimal("0")))[0]
        return ScreeningSkill(
            skill_id=skill_id,
            name=names.get(skill_id, skill_id),
            proficiency_level=proficiency,
            required=required_by_skill.get(skill_id, False),
        )

    matched = sorted(
        (skill_result(skill_id) for skill_id, met in met_by_skill.items() if met),
        key=lambda item: item.skill_id,
    )
    missing = sorted(
        (skill_result(skill_id) for skill_id, met in met_by_skill.items() if not met),
        key=lambda item: item.skill_id,
    )
    return ScreeningRead(
        application_id=application.application_id,
        score=result.score,
        pass_threshold=result.pass_threshold,
        required_skills_met=result.required_skills_met,
        outcome="pass" if result.passed else "fail",
        matched_skills=matched,
        missing_skills=missing,
        criteria=result.components,
    )


def _feedback_complete(session: Session, interview: models.Interview) -> bool:
    panel = set(
        session.scalars(
            select(models.interview_panel.c.interviewer_id).where(
                models.interview_panel.c.interview_id == interview.interview_id
            )
        ).all()
    )
    feedback = set(
        session.scalars(
            select(models.Feedback.interviewer_id).where(
                models.Feedback.interview_id == interview.interview_id
            )
        ).all()
    )
    return bool(panel) and panel <= feedback


def _require_interview_completion(session: Session, application_id: str) -> None:
    interviews = session.scalars(
        select(models.Interview).where(
            models.Interview.application_id == application_id,
            models.Interview.status != InterviewStatus.CANCELLED,
        )
    ).all()
    if not interviews:
        raise DomainError(409, "interview_required", "At least one completed interview is required.")
    for interview in interviews:
        if interview.status != InterviewStatus.COMPLETED:
            raise DomainError(
                409, "interview_incomplete", "Every interview must be completed before progression."
            )
        if not _feedback_complete(session, interview):
            raise DomainError(
                409,
                "feedback_incomplete",
                "Structured feedback from every assigned interviewer is required.",
            )
        if interview.outcome != InterviewOutcome.ADVANCE:
            raise DomainError(
                409, "interview_not_approved", "Interview feedback does not recommend advancement."
            )


def _validate_schedule(
    session: Session,
    starts_at: datetime,
    ends_at: datetime,
    interviewer_ids: list[str],
    exclude_interview_id: str | None = None,
) -> None:
    if _as_utc(starts_at) <= _now():
        raise DomainError(422, "interview_in_past", "Interviews must be scheduled in the future.")
    interviewers = session.scalars(
        select(models.Interviewer).where(
            models.Interviewer.interviewer_id.in_(interviewer_ids),
            models.Interviewer.is_active.is_(True),
        )
    ).all()
    if len(interviewers) != len(interviewer_ids):
        raise DomainError(409, "interviewer_unavailable", "An interviewer is missing or inactive.")

    for interviewer_id in interviewer_ids:
        available = session.scalar(
            select(models.InterviewerAvailability.availability_id)
            .where(
                models.InterviewerAvailability.interviewer_id == interviewer_id,
                models.InterviewerAvailability.is_available.is_(True),
                models.InterviewerAvailability.starts_at <= starts_at,
                models.InterviewerAvailability.ends_at >= ends_at,
            )
            .limit(1)
        )
        unavailable = session.scalar(
            select(models.InterviewerAvailability.availability_id)
            .where(
                models.InterviewerAvailability.interviewer_id == interviewer_id,
                models.InterviewerAvailability.is_available.is_(False),
                models.InterviewerAvailability.starts_at < ends_at,
                models.InterviewerAvailability.ends_at > starts_at,
            )
            .limit(1)
        )
        if available is None or unavailable is not None:
            raise DomainError(
                409,
                "interviewer_unavailable",
                f"Interviewer {interviewer_id} is not available for the requested interval.",
            )
        conflict_query = (
            select(models.Interview.interview_id)
            .select_from(models.Interview)
            .join(
                models.interview_panel,
                models.interview_panel.c.interview_id == models.Interview.interview_id,
            )
            .where(
                models.interview_panel.c.interviewer_id == interviewer_id,
                models.Interview.status.in_(
                    [InterviewStatus.SCHEDULED, InterviewStatus.COMPLETED]
                ),
                models.Interview.starts_at < ends_at,
                models.Interview.ends_at > starts_at,
            )
        )
        if exclude_interview_id is not None:
            conflict_query = conflict_query.where(
                models.Interview.interview_id != exclude_interview_id
            )
        if session.scalar(conflict_query.limit(1)) is not None:
            raise DomainError(
                409,
                "interviewer_conflict",
                f"Interviewer {interviewer_id} already has an overlapping interview.",
            )


def _interview_read(session: Session, interview: models.Interview) -> InterviewRead:
    interviewer_ids = session.scalars(
        select(models.interview_panel.c.interviewer_id)
        .where(models.interview_panel.c.interview_id == interview.interview_id)
        .order_by(models.interview_panel.c.interviewer_id)
    ).all()
    return InterviewRead.model_validate(
        {
            "interview_id": interview.interview_id,
            "application_id": interview.application_id,
            "round_number": interview.round_number,
            "interview_type": interview.interview_type,
            "starts_at": interview.starts_at,
            "ends_at": interview.ends_at,
            "status": interview.status,
            "outcome": interview.outcome,
            "issue_type": interview.issue_type,
            "interviewer_ids": interviewer_ids,
        }
    )


def _finding_keys(session: Session) -> set[tuple[str, str, str]]:
    return {
        (item.entity_type, item.entity_id, item.issue_type)
        for item in validate_dataset(session).findings
    }


def _exception_read(
    record: models.ExceptionRecord, finding_keys: set[tuple[str, str, str]]
) -> ExceptionRead:
    issue_type = _enum_value(record.issue_type)
    return ExceptionRead.model_validate(
        {
            "exception_id": record.exception_id,
            "issue_type": issue_type,
            "entity_type": record.entity_type,
            "entity_id": record.entity_id,
            "expected_decision": record.expected_decision,
            "observed_details": record.observed_details,
            "validation_result": record.validation_result,
            "detected_at": record.detected_at,
            "severity": SEVERITY_BY_ISSUE.get(issue_type, "medium"),
            "detected": (record.entity_type, record.entity_id, issue_type) in finding_keys,
        }
    )


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    factory = request.app.state.session_factory
    if factory is None:
        if not os.getenv("DATABASE_URL"):
            raise DomainError(
                503,
                "database_not_configured",
                "Set DATABASE_URL before using database-backed endpoints.",
            )
        factory = get_session_factory()
        request.app.state.session_factory = factory
    with factory() as session:
        session.execute(select(1))
    return {"status": "ok", "database": "connected"}


def _dashboard_application_metrics(session: Session) -> dict[str, Any]:
    screening_outcome = models.Application.screening_explanation["outcome"].as_string()
    scored = models.Application.screening_score.is_not(None)
    rows = session.execute(
        select(
            models.Application.status,
            func.count().label("applications"),
            func.count(case((scored, 1))).label("scored"),
            func.sum(models.Application.screening_score).label("score_total"),
            func.count(
                case((and_(scored, screening_outcome == "pass"), 1))
            ).label("passes"),
            func.count(
                case((and_(scored, screening_outcome == "fail"), 1))
            ).label("fails"),
        ).group_by(models.Application.status)
    ).all()
    counts = {_enum_value(row.status): row for row in rows}
    pipeline = [
        PipelineStage(stage=status, count=counts.get(status.value).applications if status.value in counts else 0)
        for status in ApplicationStatus
    ]
    scored_count = sum(row.scored for row in rows)
    score_total = sum((row.score_total or 0) for row in rows)

    return {
        "applications": sum(row.applications for row in rows),
        "pipeline": pipeline,
        "scored_applications": scored_count,
        "average_screening_score": (
            float(score_total / scored_count) if scored_count else None
        ),
        "recorded_passes": sum(row.passes for row in rows),
        "recorded_fails": sum(row.fails for row in rows),
    }


@router.get("/api/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(session: Session = Depends(get_db)) -> DashboardSummary:
    requisition_counts = (
        select(
            func.count().label("total"),
            func.count(
                case((models.JobRequisition.status == RequisitionStatus.OPEN, 1))
            ).label("open"),
        )
        .select_from(models.JobRequisition)
        .cte("requisition_counts")
    )
    candidate_counts = (
        select(func.count().label("total"))
        .select_from(models.Candidate)
        .cte("candidate_counts")
    )
    interview_counts = (
        select(func.count().label("total"))
        .select_from(models.Interview)
        .cte("interview_counts")
    )
    offer_counts = (
        select(
            func.count().label("total"),
            func.count(
                case((models.Offer.status == OfferStatus.ACCEPTED, 1))
            ).label("accepted"),
            func.count(
                case(
                    (
                        models.Offer.status.in_(
                            [OfferStatus.REJECTED, OfferStatus.DECLINED]
                        ),
                        1,
                    )
                )
            ).label("rejected"),
            func.count(
                case(
                    (
                        models.Offer.status.in_(
                            [
                                OfferStatus.PENDING_APPROVAL,
                                OfferStatus.APPROVED,
                                OfferStatus.ISSUED,
                            ]
                        ),
                        1,
                    )
                )
            ).label("pending"),
        )
        .select_from(models.Offer)
        .cte("offer_counts")
    )
    counts = session.execute(
        select(
            requisition_counts.c.total.label("total_requisitions"),
            requisition_counts.c.open.label("open_requisitions"),
            candidate_counts.c.total.label("candidates"),
            interview_counts.c.total.label("interviews"),
            offer_counts.c.total.label("offers"),
            offer_counts.c.accepted.label("accepted_offers"),
            offer_counts.c.rejected.label("rejected_offers"),
            offer_counts.c.pending.label("pending_offers"),
        )
        .select_from(requisition_counts)
        .join(candidate_counts, true())
        .join(interview_counts, true())
        .join(offer_counts, true())
    ).one()
    application_metrics = _dashboard_application_metrics(session)
    year = extract("year", models.Application.submitted_at)
    month = extract("month", models.Application.submitted_at)
    trend = [
        ApplicationTrend(
            month=f"{int(row.year):04d}-{int(row.month):02d}",
            applications=row.applications,
        )
        for row in session.execute(
            select(
                year.label("year"),
                month.label("month"),
                func.count().label("applications"),
            )
            .group_by(year, month)
            .order_by(year, month)
        )
    ]
    return DashboardSummary(
        total_requisitions=counts.total_requisitions,
        open_requisitions=counts.open_requisitions,
        candidates=counts.candidates,
        interviews=counts.interviews,
        offers=counts.offers,
        accepted_offers=counts.accepted_offers,
        rejected_offers=counts.rejected_offers,
        pending_offers=counts.pending_offers,
        pipeline=application_metrics["pipeline"],
        scored_applications=application_metrics["scored_applications"],
        average_screening_score=application_metrics["average_screening_score"],
        recorded_passes=application_metrics["recorded_passes"],
        recorded_fails=application_metrics["recorded_fails"],
        applications=application_metrics["applications"],
        application_trend=trend,
    )


@router.get("/api/dashboard/pipeline", response_model=list[PipelineStage])
def dashboard_pipeline(session: Session = Depends(get_db)) -> list[PipelineStage]:
    return _dashboard_application_metrics(session)["pipeline"]


@router.get("/api/dashboard/exceptions", response_model=list[ExceptionGroup])
def dashboard_exceptions(session: Session = Depends(get_db)) -> list[ExceptionGroup]:
    grouped: dict[tuple[str, str], int] = {}
    for finding in validate_dataset(session).findings:
        key = (finding.severity, finding.issue_type)
        grouped[key] = grouped.get(key, 0) + 1
    return [
        ExceptionGroup(severity=severity, issue_type=issue_type, count=count)
        for (severity, issue_type), count in sorted(grouped.items())
    ]


@router.get("/api/requisitions", response_model=list[RequisitionRead])
def list_requisitions(
    status: RequisitionStatus | None = None,
    department_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[models.JobRequisition]:
    statement = select(models.JobRequisition)
    if status is not None:
        statement = statement.where(models.JobRequisition.status == status)
    if department_id is not None:
        statement = statement.where(models.JobRequisition.department_id == department_id)
    return session.scalars(
        statement.order_by(models.JobRequisition.requisition_id).offset(offset).limit(limit)
    ).all()


@router.get("/api/requisitions/{requisition_id}", response_model=RequisitionRead)
def get_requisition(
    requisition_id: str, session: Session = Depends(get_db)
) -> models.JobRequisition:
    return _require(session, models.JobRequisition, "requisition_id", requisition_id, "Requisition")


@router.post("/api/requisitions", response_model=RequisitionRead, status_code=201)
def create_requisition(
    payload: RequisitionCreate, session: Session = Depends(get_db)
) -> models.JobRequisition:
    department = _require(
        session, models.Department, "department_id", payload.department_id, "Department"
    )
    if not department.is_active:
        raise DomainError(409, "department_inactive", "Requisitions require an active department.")
    skill_ids = {rule.skill_id for rule in payload.screening_rules if rule.skill_id is not None}
    existing_skill_ids = set(
        session.scalars(
            select(models.Skill.skill_id).where(
                models.Skill.skill_id.in_(skill_ids), models.Skill.is_active.is_(True)
            )
        ).all()
    )
    if existing_skill_ids != skill_ids:
        raise DomainError(
            422,
            "screening_skill_not_found",
            "Every screening skill must reference an active skill.",
            {"invalid_skill_ids": sorted(skill_ids - existing_skill_ids)},
        )
    now = _now()
    requisition = models.JobRequisition(
        requisition_id=_next_yearly_id(
            session, models.JobRequisition.requisition_id, "REQ", now.year
        ),
        department_id=payload.department_id,
        title=payload.title,
        description=payload.description,
        employment_type=payload.employment_type,
        location=payload.location,
        salary_min=payload.salary_min,
        salary_max=payload.salary_max,
        currency=payload.currency,
        headcount=payload.headcount,
        status=RequisitionStatus.DRAFT,
        created_by=payload.created_by,
        created_at=now,
    )
    session.add(requisition)
    session.flush()
    first_rule_id = _next_numbered_id(
        session, models.ScreeningRule.screening_rule_id, "RULE-"
    )
    first_rule_number = int(first_rule_id[len("RULE-") :])
    for index, rule in enumerate(payload.screening_rules):
        session.add(
            models.ScreeningRule(
                screening_rule_id=f"RULE-{first_rule_number + index:06d}",
                requisition_id=requisition.requisition_id,
                skill_id=rule.skill_id,
                rule_type=rule.rule_type,
                criterion_name=rule.criterion_name,
                weight=rule.weight,
                minimum_proficiency=rule.minimum_proficiency,
                minimum_value=rule.minimum_value,
                effective_from=now,
                is_active=True,
            )
        )
    _record_requisition_event(
        session, requisition, None, RequisitionStatus.DRAFT, payload.created_by, occurred_at=now
    )
    _audit(
        session,
        "job_requisition",
        requisition.requisition_id,
        "created",
        payload.created_by,
        payload.model_dump(mode="json"),
    )
    _commit(session)
    return requisition


@router.patch("/api/requisitions/{requisition_id}", response_model=RequisitionRead)
def patch_requisition(
    requisition_id: str,
    payload: RequisitionPatch,
    session: Session = Depends(get_db),
) -> models.JobRequisition:
    requisition = _require(
        session, models.JobRequisition, "requisition_id", requisition_id, "Requisition"
    )
    if requisition.status not in {
        RequisitionStatus.DRAFT,
        RequisitionStatus.PENDING_APPROVAL,
    }:
        raise DomainError(409, "requisition_locked", "Only unapproved requisitions may be edited.")
    changes = payload.model_dump(exclude_unset=True, exclude={"updated_by"})
    changes = {key: value for key, value in changes.items() if value is not None}
    if not changes:
        raise DomainError(422, "empty_update", "At least one requisition field must be provided.")
    salary_min = changes.get("salary_min", requisition.salary_min)
    salary_max = changes.get("salary_max", requisition.salary_max)
    if salary_max < salary_min:
        raise DomainError(422, "invalid_salary_band", "salary_max must be >= salary_min.")
    for key, value in changes.items():
        setattr(requisition, key, value)
    _audit(
        session,
        "job_requisition",
        requisition_id,
        "updated",
        payload.updated_by,
        payload.model_dump(mode="json", exclude_unset=True),
    )
    _commit(session)
    return requisition


@router.post(
    "/api/requisitions/{requisition_id}/approve", response_model=RequisitionRead
)
def approve_requisition(
    requisition_id: str,
    payload: OfferAction,
    session: Session = Depends(get_db),
) -> models.JobRequisition:
    requisition = _require(
        session, models.JobRequisition, "requisition_id", requisition_id, "Requisition"
    )
    if requisition.status != RequisitionStatus.DRAFT:
        raise DomainError(409, "invalid_requisition_state", "Only draft requisitions can be approved.")
    required_rule = session.scalar(
        select(models.ScreeningRule.screening_rule_id).where(
            models.ScreeningRule.requisition_id == requisition_id,
            models.ScreeningRule.rule_type == ScreeningRuleType.REQUIRED_SKILL,
            models.ScreeningRule.is_active.is_(True),
        )
    )
    if required_rule is None:
        raise DomainError(
            409,
            "required_screening_rule_missing",
            "A requisition needs an active required-skill rule before approval.",
        )
    approved_at = _now()
    opened_at = approved_at + timedelta(microseconds=1)
    _record_requisition_event(
        session,
        requisition,
        RequisitionStatus.DRAFT,
        RequisitionStatus.PENDING_APPROVAL,
        payload.actor,
        payload.reason,
        approved_at,
    )
    requisition.approved_by = payload.actor
    requisition.approved_at = approved_at
    _record_requisition_event(
        session,
        requisition,
        RequisitionStatus.PENDING_APPROVAL,
        RequisitionStatus.OPEN,
        payload.actor,
        payload.reason,
        opened_at,
    )
    requisition.opened_at = opened_at
    _audit(
        session,
        "job_requisition",
        requisition_id,
        "approved",
        payload.actor,
        {"approved_at": approved_at.isoformat(), "status": RequisitionStatus.OPEN.value},
    )
    _commit(session)
    return requisition


@router.post("/api/requisitions/{requisition_id}/close", response_model=RequisitionRead)
def close_requisition(
    requisition_id: str,
    payload: OfferAction,
    session: Session = Depends(get_db),
) -> models.JobRequisition:
    requisition = _require(
        session, models.JobRequisition, "requisition_id", requisition_id, "Requisition"
    )
    if requisition.status not in {RequisitionStatus.OPEN, RequisitionStatus.ON_HOLD}:
        raise DomainError(
            409, "invalid_requisition_state", "Only open or on-hold requisitions can be closed."
        )
    active_applications = session.scalar(
        select(func.count()).select_from(models.Application).where(
            models.Application.requisition_id == requisition_id,
            models.Application.status.in_(
                [
                    ApplicationStatus.APPLIED,
                    ApplicationStatus.SCREENING,
                    ApplicationStatus.INTERVIEW,
                    ApplicationStatus.OFFER,
                ]
            ),
        )
    )
    if active_applications:
        raise DomainError(
            409,
            "active_applications_exist",
            "Resolve active applications before closing the requisition.",
            {"active_application_count": active_applications},
        )
    closed_at = _now()
    _record_requisition_event(
        session,
        requisition,
        requisition.status,
        RequisitionStatus.CLOSED,
        payload.actor,
        payload.reason,
        closed_at,
    )
    requisition.closed_at = closed_at
    _audit(
        session,
        "job_requisition",
        requisition_id,
        "closed",
        payload.actor,
        {"closed_at": closed_at.isoformat(), "reason": payload.reason},
    )
    _commit(session)
    return requisition


@router.get("/api/candidates", response_model=list[CandidateRead])
def list_candidates(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[models.Candidate]:
    return session.scalars(
        select(models.Candidate)
        .order_by(models.Candidate.candidate_id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/api/candidates/{candidate_id}", response_model=CandidateRead)
def get_candidate(candidate_id: str, session: Session = Depends(get_db)) -> models.Candidate:
    return _require(session, models.Candidate, "candidate_id", candidate_id, "Candidate")


@router.get("/api/applications", response_model=list[ApplicationRead])
def list_applications(
    status: ApplicationStatus | None = None,
    candidate_id: str | None = None,
    requisition_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[models.Application]:
    statement = select(models.Application)
    if status is not None:
        statement = statement.where(models.Application.status == status)
    if candidate_id is not None:
        statement = statement.where(models.Application.candidate_id == candidate_id)
    if requisition_id is not None:
        statement = statement.where(models.Application.requisition_id == requisition_id)
    return session.scalars(
        statement.order_by(models.Application.application_id).offset(offset).limit(limit)
    ).all()


@router.get("/api/applications/{application_id}", response_model=ApplicationRead)
def get_application(
    application_id: str, session: Session = Depends(get_db)
) -> models.Application:
    return _require(session, models.Application, "application_id", application_id, "Application")


@router.post("/api/applications", response_model=ApplicationRead, status_code=201)
def create_application(
    payload: ApplicationCreate, session: Session = Depends(get_db)
) -> models.Application:
    candidate = _require(session, models.Candidate, "candidate_id", payload.candidate_id, "Candidate")
    requisition = _require(
        session, models.JobRequisition, "requisition_id", payload.requisition_id, "Requisition"
    )
    if requisition.status != RequisitionStatus.OPEN or requisition.approved_at is None:
        raise DomainError(
            409,
            "requisition_not_open",
            "Applications require an approved, open requisition.",
        )
    existing = session.scalar(
        select(models.Application).where(
            models.Application.candidate_id == candidate.candidate_id,
            models.Application.requisition_id == requisition.requisition_id,
        )
    )
    if existing is not None:
        session.add(
            models.DuplicateApplicationAttempt(
                attempt_id=uuid4().hex,
                requisition_id=requisition.requisition_id,
                candidate_id=candidate.candidate_id,
                original_application_id=existing.application_id,
                attempted_at=_now(),
                issue_type=ExceptionType.DUPLICATE_APPLICATION,
            )
        )
        _audit(
            session,
            "application",
            existing.application_id,
            "duplicate_attempt",
            "api:applicant",
            {"requisition_id": requisition.requisition_id},
        )
        _commit(session)
        raise DomainError(
            409,
            "duplicate_application",
            "This candidate already has an application for the requisition.",
        )

    submitted_at = _now()
    application = models.Application(
        application_id=f"APP-{requisition.requisition_id}-{candidate.candidate_id}",
        requisition_id=requisition.requisition_id,
        candidate_id=candidate.candidate_id,
        status=ApplicationStatus.APPLIED,
        submitted_at=submitted_at,
        source=payload.source,
        updated_at=submitted_at,
    )
    session.add(application)
    session.add(
        models.ApplicationEvent(
            event_id=uuid4().hex,
            application_id=application.application_id,
            from_status=None,
            to_status=ApplicationStatus.APPLIED,
            actor="api:applicant",
            occurred_at=submitted_at,
        )
    )
    _audit(
        session,
        "application",
        application.application_id,
        "created",
        "api:applicant",
        payload.model_dump(mode="json"),
    )
    _commit(session)
    return application


@router.get(
    "/api/applications/{application_id}/screening", response_model=ScreeningRead
)
def get_screening(
    application_id: str, session: Session = Depends(get_db)
) -> ScreeningRead:
    application = _require(
        session, models.Application, "application_id", application_id, "Application"
    )
    return _screening_response(session, application)


@router.post(
    "/api/applications/{application_id}/screen", response_model=ScreeningRead
)
def screen_application(
    application_id: str, session: Session = Depends(get_db)
) -> ScreeningRead:
    application = _require(
        session, models.Application, "application_id", application_id, "Application"
    )
    if application.status not in {ApplicationStatus.APPLIED, ApplicationStatus.SCREENING}:
        raise DomainError(
            409, "invalid_application_stage", "Only applied or screening applications can be screened."
        )
    screening_data = _screening_data(session, application)
    result, _, _ = screening_data
    response = _screening_response(session, application, screening_data)
    now = _now()
    application.screening_score = result.score
    application.screening_explanation = {
        "pass_threshold": str(result.pass_threshold),
        "required_skills_met": result.required_skills_met,
        "outcome": "pass" if result.passed else "fail",
        "criteria": [
            {
                key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in item.items()
            }
            for item in result.components
        ],
    }
    if application.status == ApplicationStatus.APPLIED:
        _record_application_event(
            session,
            application,
            ApplicationStatus.SCREENING,
            "api:screening",
            "Screening completed.",
        )
    if not result.passed:
        _record_application_event(
            session,
            application,
            ApplicationStatus.REJECTED,
            "api:screening",
            "Screening criteria were not met.",
        )
    application.updated_at = now
    _audit(
        session,
        "application",
        application_id,
        "screened",
        "api:screening",
        {
            "score": str(result.score),
            "pass_threshold": str(result.pass_threshold),
            "outcome": "pass" if result.passed else "fail",
        },
    )
    _commit(session)
    return response


@router.get("/api/interviews", response_model=list[InterviewRead])
def list_interviews(
    application_id: str | None = None,
    status: InterviewStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[InterviewRead]:
    statement = select(models.Interview)
    if application_id is not None:
        statement = statement.where(models.Interview.application_id == application_id)
    if status is not None:
        statement = statement.where(models.Interview.status == status)
    interviews = session.scalars(
        statement.order_by(models.Interview.starts_at).offset(offset).limit(limit)
    ).all()
    return [_interview_read(session, interview) for interview in interviews]


@router.get("/api/interviews/{interview_id}", response_model=InterviewRead)
def get_interview(interview_id: str, session: Session = Depends(get_db)) -> InterviewRead:
    interview = _require(session, models.Interview, "interview_id", interview_id, "Interview")
    return _interview_read(session, interview)


@router.post("/api/interviews", response_model=InterviewRead, status_code=201)
def create_interview(
    payload: InterviewCreate, session: Session = Depends(get_db)
) -> InterviewRead:
    application = _require(
        session, models.Application, "application_id", payload.application_id, "Application"
    )
    if application.status != ApplicationStatus.INTERVIEW:
        raise DomainError(
            409, "invalid_application_stage", "The application is not eligible for interviews."
        )
    screening, _, _ = _screening_data(session, application)
    if not screening.passed:
        raise DomainError(
            409, "screening_failed", "The application does not meet job-related screening criteria."
        )
    _validate_schedule(session, payload.starts_at, payload.ends_at, payload.interviewer_ids)
    interview = models.Interview(
        interview_id=_next_yearly_id(
            session,
            models.Interview.interview_id,
            "INT",
            payload.starts_at.year,
        ),
        application_id=application.application_id,
        round_number=payload.round_number,
        interview_type=payload.interview_type,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        status=InterviewStatus.SCHEDULED,
    )
    session.add(interview)
    session.flush()
    session.execute(
        insert(models.interview_panel),
        [
            {"interview_id": interview.interview_id, "interviewer_id": interviewer_id}
            for interviewer_id in payload.interviewer_ids
        ],
    )
    _audit(
        session,
        "interview",
        interview.interview_id,
        "scheduled",
        "api:recruiter",
        {
            "application_id": application.application_id,
            "starts_at": payload.starts_at.isoformat(),
            "ends_at": payload.ends_at.isoformat(),
            "interviewer_ids": payload.interviewer_ids,
        },
    )
    _commit(session)
    return _interview_read(session, interview)


@router.patch(
    "/api/interviews/{interview_id}/schedule", response_model=InterviewRead
)
def reschedule_interview(
    interview_id: str,
    payload: InterviewScheduleUpdate,
    session: Session = Depends(get_db),
) -> InterviewRead:
    interview = _require(session, models.Interview, "interview_id", interview_id, "Interview")
    if interview.status != InterviewStatus.SCHEDULED:
        raise DomainError(409, "interview_not_schedulable", "Only scheduled interviews can be changed.")
    application = _require(
        session, models.Application, "application_id", interview.application_id, "Application"
    )
    if application.status != ApplicationStatus.INTERVIEW:
        raise DomainError(
            409, "invalid_application_stage", "The application is not eligible for interviews."
        )
    _validate_schedule(
        session,
        payload.starts_at,
        payload.ends_at,
        payload.interviewer_ids,
        exclude_interview_id=interview_id,
    )
    session.execute(
        delete(models.interview_panel).where(
            models.interview_panel.c.interview_id == interview_id
        )
    )
    session.execute(
        insert(models.interview_panel),
        [
            {"interview_id": interview_id, "interviewer_id": interviewer_id}
            for interviewer_id in payload.interviewer_ids
        ],
    )
    interview.starts_at = payload.starts_at
    interview.ends_at = payload.ends_at
    _audit(
        session,
        "interview",
        interview_id,
        "rescheduled",
        "api:recruiter",
        {
            "starts_at": payload.starts_at.isoformat(),
            "ends_at": payload.ends_at.isoformat(),
            "interviewer_ids": payload.interviewer_ids,
        },
    )
    _commit(session)
    return _interview_read(session, interview)


@router.post(
    "/api/interviews/{interview_id}/feedback",
    response_model=FeedbackRead,
    status_code=201,
)
def create_feedback(
    interview_id: str,
    payload: FeedbackCreate,
    session: Session = Depends(get_db),
) -> models.Feedback:
    interview = _require(session, models.Interview, "interview_id", interview_id, "Interview")
    if interview.status not in {InterviewStatus.SCHEDULED, InterviewStatus.COMPLETED}:
        raise DomainError(409, "interview_not_feedback_eligible", "Feedback cannot be added now.")
    if _as_utc(interview.ends_at) > _now():
        raise DomainError(409, "interview_not_finished", "Feedback is accepted after the interview ends.")
    assigned = session.scalar(
        select(models.interview_panel.c.interviewer_id).where(
            models.interview_panel.c.interview_id == interview_id,
            models.interview_panel.c.interviewer_id == payload.interviewer_id,
        )
    )
    if assigned is None:
        raise DomainError(403, "interviewer_not_assigned", "Only assigned interviewers may submit feedback.")
    existing = session.scalar(
        select(models.Feedback.feedback_id).where(
            models.Feedback.interview_id == interview_id,
            models.Feedback.interviewer_id == payload.interviewer_id,
        )
    )
    if existing is not None:
        raise DomainError(409, "feedback_already_submitted", "This interviewer already submitted feedback.")
    feedback = models.Feedback(
        feedback_id=uuid4().hex,
        interview_id=interview_id,
        interviewer_id=payload.interviewer_id,
        technical_rating=payload.technical_rating,
        communication_rating=payload.communication_rating,
        role_rating=payload.role_rating,
        recommendation=payload.recommendation,
        comments=payload.comments,
        submitted_at=_now(),
    )
    session.add(feedback)
    session.flush()
    interview.status = InterviewStatus.COMPLETED
    if _feedback_complete(session, interview):
        recommendations = session.scalars(
            select(models.Feedback.recommendation).where(
                models.Feedback.interview_id == interview_id
            )
        ).all()
        weights = {
            Recommendation.STRONG_YES: 5,
            Recommendation.YES: 4,
            Recommendation.NO: 2,
            Recommendation.STRONG_NO: 1,
        }
        average = sum(weights[item] for item in recommendations) / len(recommendations)
        interview.outcome = (
            InterviewOutcome.ADVANCE
            if average >= 4
            else InterviewOutcome.REJECT
            if average <= 2
            else InterviewOutcome.HOLD
        )
    _audit(
        session,
        "interview",
        interview_id,
        "feedback_submitted",
        payload.interviewer_id,
        {"recommendation": payload.recommendation.value},
    )
    _commit(session)
    return feedback


@router.get(
    "/api/interviews/{interview_id}/feedback", response_model=list[FeedbackRead]
)
def list_feedback(
    interview_id: str, session: Session = Depends(get_db)
) -> list[models.Feedback]:
    _require(session, models.Interview, "interview_id", interview_id, "Interview")
    return session.scalars(
        select(models.Feedback)
        .where(models.Feedback.interview_id == interview_id)
        .order_by(models.Feedback.submitted_at, models.Feedback.feedback_id)
    ).all()


@router.post(
    "/api/applications/{application_id}/advance", response_model=ApplicationRead
)
def advance_application(
    application_id: str,
    payload: AdvanceRequest,
    session: Session = Depends(get_db),
) -> models.Application:
    application = _require(
        session, models.Application, "application_id", application_id, "Application"
    )
    if not is_application_transition_valid(application.status, payload.to_status):
        raise DomainError(
            409,
            "invalid_transition",
            f"Application cannot transition from {application.status.value} to {payload.to_status.value}.",
        )
    if payload.to_status in {ApplicationStatus.SCREENING, ApplicationStatus.INTERVIEW}:
        result, _, _ = _screening_data(session, application)
        if not result.passed:
            raise DomainError(
                409, "screening_failed", "The application does not meet job-related screening criteria."
            )
    if payload.to_status == ApplicationStatus.OFFER:
        _require_interview_completion(session, application_id)
        issued = session.scalar(
            select(models.Offer.offer_id).where(
                models.Offer.application_id == application_id,
                models.Offer.status == OfferStatus.ISSUED,
            )
        )
        if issued is None:
            raise DomainError(409, "offer_not_issued", "An approved offer must be issued first.")
    if payload.to_status == ApplicationStatus.HIRED:
        accepted = session.scalar(
            select(models.Offer.offer_id).where(
                models.Offer.application_id == application_id,
                models.Offer.status == OfferStatus.ACCEPTED,
            )
        )
        if accepted is None:
            raise DomainError(409, "accepted_offer_required", "An accepted offer is required.")
    if payload.to_status in {ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN} and not (
        payload.reason and payload.reason.strip()
    ):
        raise DomainError(422, "reason_required", "A reason is required for rejection or withdrawal.")
    _record_application_event(
        session, application, payload.to_status, payload.actor, payload.reason
    )
    _audit(
        session,
        "application",
        application_id,
        "stage_advanced",
        payload.actor,
        {"to_status": payload.to_status.value, "reason": payload.reason},
    )
    _commit(session)
    return application


def _require_offer_eligibility(session: Session, application: models.Application) -> None:
    if application.status != ApplicationStatus.INTERVIEW:
        raise DomainError(
            409, "invalid_application_stage", "Offers require an application in the interview stage."
        )
    _require_interview_completion(session, application.application_id)


def _validate_offer_salary(
    offer_salary: Decimal, requisition: models.JobRequisition
) -> Decimal:
    salary = offer_salary.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not requisition.salary_min <= salary <= requisition.salary_max:
        raise DomainError(
            422,
            "salary_outside_band",
            "Offer salary must be within the approved requisition salary band.",
            {
                "salary_min": str(requisition.salary_min),
                "salary_max": str(requisition.salary_max),
            },
        )
    return salary


@router.get("/api/offers", response_model=list[OfferRead])
def list_offers(
    status: OfferStatus | None = None,
    application_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[models.Offer]:
    statement = select(models.Offer)
    if status is not None:
        statement = statement.where(models.Offer.status == status)
    if application_id is not None:
        statement = statement.where(models.Offer.application_id == application_id)
    return session.scalars(
        statement.order_by(models.Offer.offer_id).offset(offset).limit(limit)
    ).all()


@router.get("/api/offers/{offer_id}", response_model=OfferRead)
def get_offer(offer_id: str, session: Session = Depends(get_db)) -> models.Offer:
    return _require(session, models.Offer, "offer_id", offer_id, "Offer")


@router.post(
    "/api/applications/{application_id}/offers",
    response_model=OfferRead,
    status_code=201,
)
def create_offer(
    application_id: str,
    payload: OfferCreate,
    session: Session = Depends(get_db),
) -> models.Offer:
    application = _require(
        session, models.Application, "application_id", application_id, "Application"
    )
    _require_offer_eligibility(session, application)
    requisition = _require(
        session,
        models.JobRequisition,
        "requisition_id",
        application.requisition_id,
        "Requisition",
    )
    salary = _validate_offer_salary(payload.salary, requisition)
    active_offer = session.scalar(
        select(models.Offer.offer_id).where(
            models.Offer.application_id == application_id,
            models.Offer.status.in_(
                [
                    OfferStatus.PENDING_APPROVAL,
                    OfferStatus.APPROVED,
                    OfferStatus.ISSUED,
                    OfferStatus.ACCEPTED,
                ]
            ),
        )
    )
    if active_offer is not None:
        raise DomainError(409, "active_offer_exists", "This application already has an active offer.")
    offer = models.Offer(
        offer_id=_next_yearly_id(session, models.Offer.offer_id, "OFF"),
        application_id=application_id,
        salary=salary,
        currency=requisition.currency,
        status=OfferStatus.PENDING_APPROVAL,
        created_by=payload.created_by,
    )
    session.add(offer)
    session.flush()
    session.add(
        models.OfferEvent(
            event_id=uuid4().hex,
            offer_id=offer.offer_id,
            from_status=None,
            to_status=OfferStatus.PENDING_APPROVAL,
            actor=payload.created_by,
            occurred_at=_now(),
        )
    )
    _audit(
        session,
        "offer",
        offer.offer_id,
        "created",
        payload.created_by,
        {"salary": str(salary), "currency": requisition.currency},
    )
    _commit(session)
    return offer


@router.post("/api/offers/{offer_id}/approve", response_model=OfferRead)
def approve_offer(
    offer_id: str, payload: OfferAction, session: Session = Depends(get_db)
) -> models.Offer:
    offer = _require(session, models.Offer, "offer_id", offer_id, "Offer")
    if offer.status != OfferStatus.PENDING_APPROVAL:
        raise DomainError(409, "offer_not_pending", "Only pending offers may be approved.")
    application = _require(
        session, models.Application, "application_id", offer.application_id, "Application"
    )
    requisition = _require(
        session,
        models.JobRequisition,
        "requisition_id",
        application.requisition_id,
        "Requisition",
    )
    offer.salary = _validate_offer_salary(offer.salary, requisition)
    offer.approved_by = payload.actor
    offer.approved_at = _now()
    _record_offer_event(
        session, offer, OfferStatus.APPROVED, payload.actor, payload.reason
    )
    _audit(
        session,
        "offer",
        offer_id,
        "approved",
        payload.actor,
        {"approved_at": offer.approved_at.isoformat()},
    )
    _commit(session)
    return offer


@router.post("/api/offers/{offer_id}/issue", response_model=OfferRead)
def issue_offer(
    offer_id: str, payload: OfferAction, session: Session = Depends(get_db)
) -> models.Offer:
    offer = _require(session, models.Offer, "offer_id", offer_id, "Offer")
    if offer.status != OfferStatus.APPROVED or not offer.approved_by or not offer.approved_at:
        raise DomainError(409, "offer_not_approved", "Offer approval is required before issuance.")
    application = _require(
        session, models.Application, "application_id", offer.application_id, "Application"
    )
    _require_offer_eligibility(session, application)
    requisition = _require(
        session,
        models.JobRequisition,
        "requisition_id",
        application.requisition_id,
        "Requisition",
    )
    offer.salary = _validate_offer_salary(offer.salary, requisition)
    offer.issued_at = _now()
    _record_offer_event(session, offer, OfferStatus.ISSUED, payload.actor, payload.reason)
    _record_application_event(
        session,
        application,
        ApplicationStatus.OFFER,
        payload.actor,
        "Approved offer issued.",
    )
    _audit(
        session,
        "offer",
        offer_id,
        "issued",
        payload.actor,
        {"issued_at": offer.issued_at.isoformat()},
    )
    _commit(session)
    return offer


@router.post("/api/offers/{offer_id}/respond", response_model=OfferRead)
def respond_to_offer(
    offer_id: str,
    payload: OfferResponseRequest,
    session: Session = Depends(get_db),
) -> models.Offer:
    offer = _require(session, models.Offer, "offer_id", offer_id, "Offer")
    if offer.status != OfferStatus.ISSUED or offer.responded_at is not None:
        raise DomainError(409, "offer_not_issued", "Only unanswered issued offers may receive a response.")
    application = _require(
        session, models.Application, "application_id", offer.application_id, "Application"
    )
    target_offer_status = (
        OfferStatus.ACCEPTED
        if payload.decision == "accepted"
        else OfferStatus.REJECTED
    )
    target_application_status = (
        ApplicationStatus.HIRED
        if payload.decision == "accepted"
        else ApplicationStatus.REJECTED
    )
    if application.status != ApplicationStatus.OFFER:
        raise DomainError(
            409, "application_offer_mismatch", "The application must be in the offer stage."
        )
    offer.responded_at = _now()
    _record_offer_event(
        session, offer, target_offer_status, payload.actor, payload.reason
    )
    _record_application_event(
        session,
        application,
        target_application_status,
        payload.actor,
        payload.reason or f"Offer {payload.decision}.",
    )
    _audit(
        session,
        "offer",
        offer_id,
        payload.decision,
        payload.actor,
        {"responded_at": offer.responded_at.isoformat(), "reason": payload.reason},
    )
    _commit(session)
    return offer


@router.get("/api/audit", response_model=list[AuditRead])
def list_audit(
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[models.AuditEvent]:
    statement = select(models.AuditEvent)
    if entity_type is not None:
        statement = statement.where(models.AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        statement = statement.where(models.AuditEvent.entity_id == entity_id)
    return session.scalars(
        statement.order_by(models.AuditEvent.occurred_at.desc()).offset(offset).limit(limit)
    ).all()


@router.get("/api/exceptions", response_model=list[ExceptionRead])
def list_exceptions(
    issue_type: ExceptionType | None = None,
    entity_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[ExceptionRead]:
    statement = select(models.ExceptionRecord)
    if issue_type is not None:
        statement = statement.where(models.ExceptionRecord.issue_type == issue_type)
    if entity_type is not None:
        statement = statement.where(models.ExceptionRecord.entity_type == entity_type)
    records = session.scalars(
        statement.order_by(models.ExceptionRecord.detected_at, models.ExceptionRecord.exception_id)
        .offset(offset)
        .limit(limit)
    ).all()
    finding_keys = _finding_keys(session)
    return [_exception_read(record, finding_keys) for record in records]


@router.get("/api/exceptions/{exception_id}", response_model=ExceptionRead)
def get_exception(
    exception_id: str, session: Session = Depends(get_db)
) -> ExceptionRead:
    record = _require(
        session, models.ExceptionRecord, "exception_id", exception_id, "Exception"
    )
    return _exception_read(record, _finding_keys(session))


def create_app(session_factory: sessionmaker[Any] | None = None) -> FastAPI:
    app = FastAPI(title="Recruitment Management API", version="1.0.0")
    app.state.session_factory = session_factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in os.getenv(
                "CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000,"
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if origin.strip()
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": {"path": str(request.url.path), "context": exc.details},
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_error",
                    "message": "The request does not match the required schema.",
                    "details": {
                        "path": str(request.url.path),
                        "errors": jsonable_encoder(exc.errors()),
                    },
                }
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "database_conflict",
                    "message": "The request conflicts with an existing record or database constraint.",
                    "details": {
                        "path": str(request.url.path),
                        "error_type": type(exc).__name__,
                    },
                }
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "database_unavailable",
                    "message": "The database operation could not be completed.",
                    "details": {
                        "path": str(request.url.path),
                        "error_type": type(exc).__name__,
                    },
                }
            },
        )

    return app


app = create_app()
