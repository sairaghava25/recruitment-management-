"""Deterministic synthetic data generation and dataset validation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session

from . import models
from .database import Base
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
    ResumeStatus,
    ScreeningRuleType,
)
from .services import (
    calculate_screening_score,
    is_application_transition_valid,
    is_lifecycle_transition_valid,
)


SEVERITY_BY_ISSUE = {
    "unavailable_interviewer": "high",
    "duplicate_application": "medium",
    "missing_feedback_advancement": "high",
    "offer_outside_salary_band": "high",
    "closed_requisition_active_applications": "high",
    "invalid_application_stage_transition": "high",
    "invalid_lifecycle_transition": "high",
    "missing_required_relationship": "high",
    "invalid_offer_response": "high",
    "incomplete_offer_history": "high",
    "application_offer_status_mismatch": "high",
}

@dataclass(frozen=True)
class ValidationFinding:
    entity_type: str
    entity_id: str
    issue_type: str
    severity: str
    details: str

@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    violation_count: int
    details: tuple[str, ...]

@dataclass(frozen=True)
class ValidationReport:
    checks: tuple[CheckResult, ...]
    findings: tuple[ValidationFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        passed = sum(check.passed for check in self.checks)
        failed = len(self.checks) - passed
        return {
            "total_checks": len(self.checks),
            "passed_checks": passed,
            "failed_checks": failed,
            "detected_anomalies": len(self.findings),
            "checks": [asdict(check) for check in self.checks],
            "findings": [asdict(finding) for finding in self.findings],
        }

def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def _status(value: Any) -> str:
    return value.value if isinstance(value, Enum) else str(value)

def _row_value(row: Any, column: str) -> Any:
    if isinstance(row, Mapping):
        return row[column]
    return getattr(row, column)

def _finding(
    entity_type: str,
    entity_id: str,
    issue_type: str,
    details: str,
) -> ValidationFinding:
    return ValidationFinding(
        entity_type=entity_type,
        entity_id=entity_id,
        issue_type=issue_type,
        severity=SEVERITY_BY_ISSUE.get(issue_type, "medium"),
        details=details,
    )

def validate_dataset(session: Session) -> ValidationReport:
    """Run schema, relationship, lifecycle, and business-rule checks."""
    findings: list[ValidationFinding] = []
    checks: list[CheckResult] = []

    def record_check(name: str, items: list[ValidationFinding]) -> None:
        findings.extend(items)
        checks.append(
            CheckResult(
                name=name,
                passed=not items,
                violation_count=len(items),
                details=tuple(
                    f"{item.entity_type} {item.entity_id}: {item.details}" for item in items
                ),
            )
        )

    loaded_by_table: dict[str, list[Any]] = {}
    model_rows: dict[str, list[Any]] = {}
    for mapper in Base.registry.mappers:
        model = mapper.class_
        rows = list(session.scalars(select(model)).all())
        model_rows[model.__tablename__] = rows
        loaded_by_table[model.__tablename__] = rows

    panel_rows = list(session.execute(select(models.interview_panel)).mappings().all())
    loaded_by_table[models.interview_panel.name] = panel_rows

    primary_key_issues: list[ValidationFinding] = []
    for table in Base.metadata.sorted_tables:
        pk_columns = list(table.primary_key.columns)
        seen: set[tuple[Any, ...]] = set()
        for row in loaded_by_table.get(table.name, []):
            key = tuple(_row_value(row, column.name) for column in pk_columns)
            if key in seen:
                primary_key_issues.append(
                    _finding(table.name, "|".join(map(str, key)), "duplicate_application", "duplicate primary key")
                )
            seen.add(key)
    record_check("primary_key_uniqueness", primary_key_issues)

    foreign_key_issues: list[ValidationFinding] = []
    for table in Base.metadata.sorted_tables:
        source_rows = loaded_by_table.get(table.name, [])
        for constraint in table.foreign_key_constraints:
            elements = list(constraint.elements)
            parent_table = elements[0].column.table
            parent_rows = loaded_by_table.get(parent_table.name, [])
            parent_keys = {
                tuple(_row_value(row, element.column.name) for element in elements)
                for row in parent_rows
            }
            source_columns = [element.parent.name for element in elements]
            for row in source_rows:
                key = tuple(_row_value(row, column) for column in source_columns)
                if all(value is None for value in key):
                    continue
                if key not in parent_keys:
                    pk_values = tuple(
                        _row_value(row, column.name) for column in table.primary_key.columns
                    )
                    foreign_key_issues.append(
                        _finding(
                            table.name,
                            "|".join(map(str, pk_values)),
                            "missing_required_relationship",
                            f"foreign key to {parent_table.name} does not resolve",
                        )
                    )
    record_check("foreign_key_integrity", foreign_key_issues)

    duplicate_issues: list[ValidationFinding] = []
    app_pairs: dict[tuple[str, str], list[models.Application]] = defaultdict(list)
    for application in model_rows["applications"]:
        app_pairs[(application.requisition_id, application.candidate_id)].append(application)
    for pair, applications in app_pairs.items():
        if len(applications) > 1:
            for application in applications[1:]:
                duplicate_issues.append(
                    _finding("Application", application.application_id, "duplicate_application", f"duplicate pair {pair}")
                )
    applications_by_id = {row.application_id: row for row in model_rows["applications"]}
    for attempt in model_rows["duplicate_application_attempts"]:
        original = applications_by_id.get(attempt.original_application_id)
        if (
            original is None
            or original.requisition_id != attempt.requisition_id
            or original.candidate_id != attempt.candidate_id
        ):
            duplicate_issues.append(
                _finding(
                    "DuplicateApplicationAttempt",
                    attempt.attempt_id,
                    "duplicate_application",
                    "attempt does not match its original application",
                )
            )
        else:
            duplicate_issues.append(
                _finding(
                    "DuplicateApplicationAttempt",
                    attempt.attempt_id,
                    "duplicate_application",
                    f"repeated submission for {attempt.requisition_id} and {attempt.candidate_id}",
                )
            )
    record_check("duplicate_application_detection", duplicate_issues)

    interviewers_by_id = {row.interviewer_id: row for row in model_rows["interviewers"]}
    availability_by_interviewer: dict[str, list[models.InterviewerAvailability]] = defaultdict(list)
    for availability in model_rows["interviewer_availability"]:
        availability_by_interviewer[availability.interviewer_id].append(availability)
    panel_by_interview: dict[str, list[str]] = defaultdict(list)
    for panel_row in panel_rows:
        panel_by_interview[panel_row["interview_id"]].append(panel_row["interviewer_id"])
    availability_issues: list[ValidationFinding] = []
    for interview in model_rows["interviews"]:
        for interviewer_id in panel_by_interview.get(interview.interview_id, []):
            windows = availability_by_interviewer.get(interviewer_id, [])
            starts_at = _utc(interview.starts_at)
            ends_at = _utc(interview.ends_at)
            unavailable_overlap = any(
                not window.is_available
                and _utc(window.starts_at) < ends_at
                and _utc(window.ends_at) > starts_at
                for window in windows
            )
            covered_by_available_window = any(
                window.is_available
                and _utc(window.starts_at) <= starts_at
                and _utc(window.ends_at) >= ends_at
                for window in windows
            )
            if unavailable_overlap or not covered_by_available_window:
                interviewer = interviewers_by_id.get(interviewer_id)
                label = interviewer.display_name if interviewer else interviewer_id
                availability_issues.append(
                    _finding(
                        "Interview",
                        interview.interview_id,
                        "unavailable_interviewer",
                        f"panelist {label} is unavailable for the scheduled interval",
                    )
                )
    record_check("interviewer_availability_conflicts", availability_issues)

    feedback_by_interview: dict[str, set[str]] = defaultdict(set)
    for feedback in model_rows["feedback"]:
        feedback_by_interview[feedback.interview_id].add(feedback.interviewer_id)
    feedback_issues: list[ValidationFinding] = []
    for interview in model_rows["interviews"]:
        if _status(interview.status) != "completed":
            continue
        required_panelists = set(panel_by_interview.get(interview.interview_id, []))
        missing = sorted(required_panelists - feedback_by_interview.get(interview.interview_id, set()))
        if missing:
            feedback_issues.append(
                _finding(
                    "Interview",
                    interview.interview_id,
                    "missing_feedback_advancement",
                    f"completed interview lacks feedback from {', '.join(missing)}",
                )
            )
    record_check("missing_mandatory_feedback", feedback_issues)

    events_by_application: dict[str, list[models.ApplicationEvent]] = defaultdict(list)
    for event in model_rows["application_events"]:
        events_by_application[event.application_id].append(event)
    stage_issues: list[ValidationFinding] = []
    for application in model_rows["applications"]:
        events = sorted(
            events_by_application.get(application.application_id, []),
            key=lambda row: (_utc(row.occurred_at), row.event_id),
        )
        previous: str | None = None
        for event in events:
            current = _status(event.to_status)
            from_status = _status(event.from_status) if event.from_status is not None else None
            if from_status != previous or not is_application_transition_valid(previous, current):
                stage_issues.append(
                    _finding(
                        "Application",
                        application.application_id,
                        "invalid_application_stage_transition",
                        f"invalid transition {previous!r} -> {current!r}",
                    )
                )
                break
            previous = current
        if not events or previous != _status(application.status):
            stage_issues.append(
                _finding(
                    "Application",
                    application.application_id,
                    "invalid_application_stage_transition",
                    "current status does not match its lifecycle history",
                )
            )
    record_check("application_stage_transitions", stage_issues)

    requisitions_by_id = {row.requisition_id: row for row in model_rows["job_requisitions"]}
    offer_events_by_id: dict[str, list[models.OfferEvent]] = defaultdict(list)
    for event in model_rows["offer_events"]:
        offer_events_by_id[event.offer_id].append(event)
    salary_issues: list[ValidationFinding] = []
    offer_approval_issues: list[ValidationFinding] = []
    offer_response_issues: list[ValidationFinding] = []
    offer_history_issues: list[ValidationFinding] = []
    offers_by_application: dict[str, list[models.Offer]] = defaultdict(list)
    for offer in model_rows["offers"]:
        offers_by_application[offer.application_id].append(offer)
        application = applications_by_id.get(offer.application_id)
        requisition = requisitions_by_id.get(application.requisition_id) if application else None
        if requisition and not (requisition.salary_min <= offer.salary <= requisition.salary_max):
            salary_issues.append(
                _finding(
                    "Offer",
                    offer.offer_id,
                    "offer_outside_salary_band",
                    f"salary {offer.salary} is outside {requisition.salary_min}-{requisition.salary_max}",
                )
            )
        status = _status(offer.status)
        if status in {"approved", "issued", "accepted", "declined", "rejected"} and (
            not offer.approved_by or not offer.approved_at
        ):
            offer_approval_issues.append(
                _finding("Offer", offer.offer_id, "missing_required_relationship", "approved offer lacks approver metadata")
            )
        if status in {"issued", "accepted", "declined", "rejected"} and not offer.issued_at:
            offer_approval_issues.append(
                _finding("Offer", offer.offer_id, "missing_required_relationship", "issued offer lacks issued_at")
            )
        events = sorted(
            offer_events_by_id.get(offer.offer_id, []),
            key=lambda row: (_utc(row.occurred_at), row.event_id),
        )
        event_by_status: dict[str, list[models.OfferEvent]] = defaultdict(list)
        for event in events:
            event_by_status[_status(event.to_status)].append(event)
        for field_name, transition_status in (
            ("approved_at", "approved"),
            ("issued_at", "issued"),
        ):
            timestamp = getattr(offer, field_name)
            if timestamp is not None and not any(
                _utc(event.occurred_at) == _utc(timestamp)
                for event in event_by_status.get(transition_status, [])
            ):
                offer_history_issues.append(
                    _finding(
                        "Offer",
                        offer.offer_id,
                        "incomplete_offer_history",
                        f"{field_name} has no matching {transition_status} event",
                    )
                )
        terminal_statuses = {"accepted", "declined", "rejected"}
        if status in terminal_statuses:
            response_events = event_by_status.get(status, [])
            matching_response = [
                event
                for event in response_events
                if offer.responded_at is not None
                and _utc(event.occurred_at) == _utc(offer.responded_at)
            ]
            if offer.responded_at is None:
                offer_response_issues.append(
                    _finding(
                        "Offer",
                        offer.offer_id,
                        "invalid_offer_response",
                        f"{status} offer has no response date",
                    )
                )
            if not matching_response:
                offer_history_issues.append(
                    _finding(
                        "Offer",
                        offer.offer_id,
                        "incomplete_offer_history",
                        f"{status} response has no event at responded_at",
                    )
                )
            elif not any(event.reason and event.reason.strip() for event in matching_response):
                offer_response_issues.append(
                    _finding(
                        "Offer",
                        offer.offer_id,
                        "invalid_offer_response",
                        f"{status} offer has no matching response event with a reason",
                    )
                )
        elif status in {"pending_approval", "approved", "issued"} and offer.responded_at is not None:
            offer_response_issues.append(
                _finding(
                    "Offer",
                    offer.offer_id,
                    "invalid_offer_response",
                    f"{status} offer must not have a response date",
                )
            )
        if not event_by_status.get("pending_approval"):
            offer_history_issues.append(
                _finding("Offer", offer.offer_id, "incomplete_offer_history", "offer has no pending-approval event")
            )
    record_check("salary_band_violations", salary_issues)
    record_check("offer_approval_conditions", offer_approval_issues)
    record_check("offer_response_dates_and_reasons", offer_response_issues)
    record_check("offer_status_history_completeness", offer_history_issues)

    application_offer_issues: list[ValidationFinding] = []
    expected_application_status = {
        "pending_approval": "offer",
        "approved": "offer",
        "issued": "offer",
        "accepted": "hired",
        "declined": "rejected",
        "rejected": "rejected",
        "withdrawn": "withdrawn",
    }
    for application_id, application_offers in offers_by_application.items():
        latest_offer = max(
            application_offers,
            key=lambda offer: (
                _utc(
                    offer.responded_at
                    or offer.issued_at
                    or offer.approved_at
                    or datetime.min.replace(tzinfo=timezone.utc)
                ),
                offer.offer_id,
            ),
        )
        expected_status = expected_application_status[_status(latest_offer.status)]
        application = applications_by_id.get(application_id)
        if application is None or _status(application.status) != expected_status:
            observed = _status(application.status) if application else "missing"
            application_offer_issues.append(
                _finding(
                    "Application",
                    application_id,
                    "application_offer_status_mismatch",
                    f"application status {observed} does not match final offer status {_status(latest_offer.status)}",
                )
            )
    record_check("application_offer_status_consistency", application_offer_issues)

    active_statuses = {"applied", "screening", "interview", "offer"}
    applications_by_requisition: dict[str, list[models.Application]] = defaultdict(list)
    for application in model_rows["applications"]:
        applications_by_requisition[application.requisition_id].append(application)
    closed_issues: list[ValidationFinding] = []
    for requisition in model_rows["job_requisitions"]:
        if _status(requisition.status) != "closed":
            continue
        active = [
            row.application_id
            for row in applications_by_requisition.get(requisition.requisition_id, [])
            if _status(row.status) in active_statuses
        ]
        if active:
            closed_issues.append(
                _finding(
                    "JobRequisition",
                    requisition.requisition_id,
                    "closed_requisition_active_applications",
                    f"closed requisition has {len(active)} active application(s)",
                )
            )
    record_check("closed_requisition_consistency", closed_issues)

    date_issues: list[ValidationFinding] = []
    for requisition in model_rows["job_requisitions"]:
        if requisition.approved_at and requisition.created_at and _utc(requisition.approved_at) < _utc(requisition.created_at):
            date_issues.append(_finding("JobRequisition", requisition.requisition_id, "invalid_lifecycle_transition", "approval predates creation"))
        if requisition.opened_at and requisition.approved_at and _utc(requisition.opened_at) < _utc(requisition.approved_at):
            date_issues.append(_finding("JobRequisition", requisition.requisition_id, "invalid_lifecycle_transition", "opening predates approval"))
        if requisition.closed_at and requisition.opened_at and _utc(requisition.closed_at) < _utc(requisition.opened_at):
            date_issues.append(_finding("JobRequisition", requisition.requisition_id, "invalid_lifecycle_transition", "closure predates opening"))
    candidates_by_id = {row.candidate_id: row for row in model_rows["candidates"]}
    for application in model_rows["applications"]:
        candidate = candidates_by_id.get(application.candidate_id)
        requisition = requisitions_by_id.get(application.requisition_id)
        if candidate and _utc(application.submitted_at) < _utc(candidate.created_at):
            date_issues.append(_finding("Application", application.application_id, "invalid_lifecycle_transition", "submission predates candidate creation"))
        if requisition and requisition.opened_at and _utc(application.submitted_at) < _utc(requisition.opened_at):
            date_issues.append(_finding("Application", application.application_id, "invalid_lifecycle_transition", "submission predates requisition opening"))
    interviews_by_id = {row.interview_id: row for row in model_rows["interviews"]}
    for interview in model_rows["interviews"]:
        if _utc(interview.ends_at) <= _utc(interview.starts_at):
            date_issues.append(_finding("Interview", interview.interview_id, "invalid_lifecycle_transition", "interview end does not follow start"))
        app = applications_by_id.get(interview.application_id)
        if app and _utc(interview.starts_at) < _utc(app.submitted_at):
            date_issues.append(_finding("Interview", interview.interview_id, "invalid_lifecycle_transition", "interview predates application"))
    for feedback in model_rows["feedback"]:
        interview = interviews_by_id.get(feedback.interview_id)
        if interview and _utc(feedback.submitted_at) < _utc(interview.ends_at):
            date_issues.append(_finding("Feedback", feedback.feedback_id, "invalid_lifecycle_transition", "feedback predates interview completion"))
    for offer in model_rows["offers"]:
        dates = [offer.approved_at, offer.issued_at, offer.responded_at]
        normalized = [_utc(value) for value in dates if value is not None]
        if any(later < earlier for earlier, later in zip(normalized, normalized[1:])):
            date_issues.append(_finding("Offer", offer.offer_id, "invalid_lifecycle_transition", "offer timestamps are out of order"))
    record_check("date_ordering", date_issues)

    valid_states = {
        "job_requisitions": {value.value for value in models.RequisitionStatus},
        "applications": {value.value for value in models.ApplicationStatus},
        "interviews": {value.value for value in models.InterviewStatus},
        "offers": {value.value for value in models.OfferStatus},
        "resume_artifacts": {value.value for value in models.ResumeStatus},
    }
    status_columns = {
        "job_requisitions": "status",
        "applications": "status",
        "interviews": "status",
        "offers": "status",
        "resume_artifacts": "extraction_status",
    }
    state_issues: list[ValidationFinding] = []
    for table_name, allowed in valid_states.items():
        for row in model_rows[table_name]:
            value = _status(getattr(row, status_columns[table_name]))
            if value not in allowed:
                state_issues.append(_finding(table_name, str(getattr(row, list(row.__mapper__.primary_key)[0].key)), "invalid_lifecycle_transition", f"unknown state {value}"))
    record_check("valid_lifecycle_states", state_issues)

    requisition_events_by_id: dict[str, list[models.RequisitionEvent]] = defaultdict(list)
    for event in model_rows["requisition_events"]:
        requisition_events_by_id[event.requisition_id].append(event)
    offer_events_by_id: dict[str, list[models.OfferEvent]] = defaultdict(list)
    for event in model_rows["offer_events"]:
        offer_events_by_id[event.offer_id].append(event)
    lifecycle_issues: list[ValidationFinding] = []
    for entity_type, groups, current_by_id, status_field in (
        ("requisition", requisition_events_by_id, requisitions_by_id, "status"),
        ("offer", offer_events_by_id, {row.offer_id: row for row in model_rows["offers"]}, "status"),
    ):
        for entity_id, entity_events in groups.items():
            ordered = sorted(entity_events, key=lambda row: (_utc(row.occurred_at), row.event_id))
            previous: str | None = None
            for event in ordered:
                current = _status(event.to_status)
                from_status = _status(event.from_status) if event.from_status is not None else None
                if from_status != previous or not is_lifecycle_transition_valid(
                    entity_type, previous, current
                ):
                    lifecycle_issues.append(_finding("JobRequisition" if entity_type == "requisition" else "Offer", entity_id, "invalid_lifecycle_transition", f"invalid {entity_type} transition {previous!r} -> {current!r}"))
                    break
                previous = current
            entity = current_by_id.get(entity_id)
            if entity is None or previous != _status(getattr(entity, status_field)):
                lifecycle_issues.append(_finding("JobRequisition" if entity_type == "requisition" else "Offer", entity_id, "invalid_lifecycle_transition", f"{entity_type} status does not match its event history"))
    record_check("requisition_and_offer_lifecycle", lifecycle_issues)

    relationship_issues: list[ValidationFinding] = []
    screening_rules_by_requisition: dict[str, list[models.ScreeningRule]] = defaultdict(list)
    for rule in model_rows["screening_rules"]:
        screening_rules_by_requisition[rule.requisition_id].append(rule)
        rule_type = _status(rule.rule_type)
        if rule_type in {"required_skill", "weighted_skill"} and not rule.skill_id:
            relationship_issues.append(_finding("ScreeningRule", rule.screening_rule_id, "missing_required_relationship", "skill rule has no skill"))
    for requisition in model_rows["job_requisitions"]:
        rules = screening_rules_by_requisition.get(requisition.requisition_id, [])
        if not any(_status(rule.rule_type) == "required_skill" for rule in rules):
            relationship_issues.append(_finding("JobRequisition", requisition.requisition_id, "missing_required_relationship", "requisition has no required skill rule"))
    for interview in model_rows["interviews"]:
        if not panel_by_interview.get(interview.interview_id):
            relationship_issues.append(_finding("Interview", interview.interview_id, "missing_required_relationship", "interview has no panel members"))
    for resume in model_rows["resume_artifacts"]:
        if resume.application_id:
            application = applications_by_id.get(resume.application_id)
            if application and application.candidate_id != resume.candidate_id:
                relationship_issues.append(_finding("ResumeArtifact", resume.resume_id, "missing_required_relationship", "resume candidate differs from application candidate"))
    record_check("required_relationship_integrity", relationship_issues)

    return ValidationReport(tuple(checks), tuple(findings))

def compare_ground_truth(
    report: ValidationReport, ground_truth: list[dict[str, str]]
) -> dict[str, Any]:
    """Compare output from validation with labels after validation has completed."""
    detected = Counter(
        (finding.entity_type, finding.entity_id, finding.issue_type, finding.severity)
        for finding in report.findings
    )
    expected_keys = Counter(
        (
            item["entity_type"],
            item["entity_id"],
            item["issue_type"],
            item["expected_severity"],
        )
        for item in ground_truth
        if item["expected_detection_result"] == "detected"
    )
    missed = expected_keys - detected
    unexpected = detected - expected_keys
    return {
        **report.to_dict(),
        "known_anomaly_count": len(ground_truth),
        "undetected_known_anomalies": sum(missed.values()),
        "unexpected_anomalies": sum(unexpected.values()),
        "validation_passed": not missed and not unexpected,
        "undetected_details": [
            {
                "entity_type": key[0],
                "entity_id": key[1],
                "issue_type": key[2],
                "severity": key[3],
                "count": count,
            }
            for key, count in sorted(missed.items())
        ],
        "unexpected_details": [
            {
                "entity_type": key[0],
                "entity_id": key[1],
                "issue_type": key[2],
                "severity": key[3],
                "count": count,
            }
            for key, count in sorted(unexpected.items())
        ],
    }

SEED = 20261022

UTC = timezone.utc

BASE_TIME = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)

AVAILABILITY_END = datetime(2027, 1, 1, 0, 0, tzinfo=UTC)

ANOMALY_CLOSE_TIME = datetime(2026, 12, 15, 12, 0, tzinfo=UTC)

OUTPUT_DIRECTORY = Path(__file__).resolve().parents[2] / "generated-data"

DATASET_SIZES = {
    "starter": {
        "requisitions": 10,
        "candidates": 100,
        "applications": 150,
        "interviews": 30,
        "offers": 8,
    },
    "full": {
        "requisitions": 100,
        "candidates": 2000,
        "applications": 3000,
        "interviews": 600,
        "offers": 150,
    },
}

DEPARTMENTS = (
    ("Engineering", "Software delivery and platform engineering."),
    ("Data", "Analytics, data engineering, and applied data science."),
    ("Product", "Product planning and product operations."),
    ("Design", "Product and service design."),
    ("Security", "Information security and application assurance."),
    ("Operations", "Business and technical operations."),
    ("Customer Success", "Customer enablement and support."),
    ("Finance", "Financial planning and business finance."),
)

SKILLS = (
    ("Python", "Programming"),
    ("SQL", "Data"),
    ("PostgreSQL", "Data"),
    ("Data Modeling", "Data"),
    ("Machine Learning", "Data"),
    ("Cloud Architecture", "Infrastructure"),
    ("Kubernetes", "Infrastructure"),
    ("Distributed Systems", "Infrastructure"),
    ("Java", "Programming"),
    ("TypeScript", "Programming"),
    ("API Design", "Software Engineering"),
    ("Testing", "Software Engineering"),
    ("Product Analytics", "Product"),
    ("Product Strategy", "Product"),
    ("UX Research", "Design"),
    ("Interaction Design", "Design"),
    ("Threat Modeling", "Security"),
    ("Application Security", "Security"),
    ("Incident Response", "Security"),
    ("Project Planning", "Operations"),
    ("Process Improvement", "Operations"),
    ("Technical Writing", "Communication"),
    ("Financial Modeling", "Finance"),
    ("Customer Enablement", "Customer Success"),
)

FIRST_NAMES = (
    "Alex", "Avery", "Blake", "Cameron", "Casey", "Drew", "Elliot", "Emery",
    "Finley", "Harper", "Jamie", "Jordan", "Kai", "Logan", "Morgan", "Parker",
    "Quinn", "Reese", "Riley", "Robin", "Rowan", "Sam", "Skyler", "Taylor",
)

LAST_NAMES = (
    "Alden", "Bennett", "Brooks", "Carter", "Chen", "Ellis", "Foster", "Gray",
    "Hayes", "Jordan", "Kim", "Lane", "Morgan", "Nguyen", "Parker", "Reed",
    "Rivera", "Shah", "Turner", "Wells", "Young", "Zhou",
)

LOCATIONS = (
    "Austin, TX", "Boston, MA", "Chicago, IL", "Denver, CO", "Madison, WI",
    "New York, NY", "Portland, OR", "Raleigh, NC", "Seattle, WA", "Remote",
)

SOURCES = ("career_site", "professional_network", "employee_referral", "job_board")

class GenerationError(RuntimeError):
    """Raised when the requested deterministic dataset cannot meet its targets."""

def _decimal(value: float | int | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()

def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value

def _stable_json(value: Any) -> bytes:
    return (
        json.dumps(
            _json_value(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")

def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return _stable_json(value).decode("utf-8").rstrip("\n")
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)

def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        value = _iso(value)
    if isinstance(value, (dict, list, tuple)):
        serialized = _stable_json(value).decode("utf-8").rstrip("\n")
        escaped = serialized.replace("'", "''")
        return f"CAST('{escaped}' AS JSON)"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"

def _make_row_lists() -> dict[str, list[dict[str, Any]]]:
    return {table.name: [] for table in Base.metadata.sorted_tables}

def _next_id(prefix: str, number: int, width: int = 6) -> str:
    return f"{prefix}{number:0{width}d}"

def _score_candidate(
    candidate_skills: dict[str, tuple[int, Decimal]],
    rules: list[dict[str, Any]],
) -> tuple[Decimal, bool, list[dict[str, Any]]]:
    result = calculate_screening_score(candidate_skills, rules)
    return result.score, result.required_skills_met, result.components

def _generate_baseline(size_name: str) -> dict[str, list[dict[str, Any]]]:
    size = DATASET_SIZES[size_name]
    rng = random.Random(SEED)
    rows = _make_row_lists()
    department_ids: list[str] = []
    skill_ids: list[str] = []

    for index, (name, description) in enumerate(DEPARTMENTS, start=1):
        department_id = _next_id("DEPT-", index, 4)
        department_ids.append(department_id)
        rows["departments"].append(
            {
                "department_id": department_id,
                "name": name,
                "description": description,
                "is_active": True,
                "created_at": BASE_TIME,
                "updated_at": BASE_TIME,
            }
        )

    for index, (name, category) in enumerate(SKILLS, start=1):
        skill_id = _next_id("SKILL-", index, 4)
        skill_ids.append(skill_id)
        rows["skills"].append(
            {
                "skill_id": skill_id,
                "name": name,
                "category": category,
                "is_active": True,
            }
        )

    requisition_skills: dict[str, list[str]] = {}
    requisition_by_id: dict[str, dict[str, Any]] = {}
    rules_by_requisition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index in range(1, size["requisitions"] + 1):
        requisition_id = f"REQ-2026-{index:06d}"
        department_id = department_ids[(index - 1) % len(department_ids)]
        chosen_skills = rng.sample(skill_ids, 3)
        title_skill = SKILLS[skill_ids.index(chosen_skills[0])][0]
        created_at = BASE_TIME + timedelta(minutes=index)
        approved_at = created_at + timedelta(days=2)
        opened_at = created_at + timedelta(days=4)
        salary_min = Decimal("70000.00") + Decimal((index % 8) * 5000)
        salary_max = salary_min + Decimal("50000.00")
        requisition = {
            "requisition_id": requisition_id,
            "department_id": department_id,
            "title": f"{title_skill} Engineer",
            "description": (
                f"Design, build, and maintain role-related systems using {title_skill}. "
                "Collaborate with cross-functional partners and document measurable outcomes."
            ),
            "employment_type": EmploymentType.FULL_TIME,
            "location": LOCATIONS[(index - 1) % len(LOCATIONS)],
            "salary_min": salary_min,
            "salary_max": salary_max,
            "currency": "USD",
            "headcount": 1 + int(index % 3 == 0),
            "status": RequisitionStatus.OPEN,
            "created_by": "synthetic.recruiting@example.test",
            "approved_by": "synthetic.finance@example.test",
            "approved_at": approved_at,
            "opened_at": opened_at,
            "closed_at": None,
            "created_at": created_at,
        }
        rows["job_requisitions"].append(requisition)
        requisition_by_id[requisition_id] = requisition
        requisition_skills[requisition_id] = chosen_skills

        rules = [
            {
                "screening_rule_id": _next_id("RULE-", (index - 1) * 5 + 1, 6),
                "requisition_id": requisition_id,
                "skill_id": chosen_skills[0],
                "rule_type": ScreeningRuleType.REQUIRED_SKILL,
                "criterion_name": f"Required: {SKILLS[skill_ids.index(chosen_skills[0])][0]}",
                "weight": Decimal("30.00"),
                "minimum_proficiency": 2,
                "minimum_value": None,
                "effective_from": opened_at,
                "effective_to": None,
                "is_active": True,
            },
            {
                "screening_rule_id": _next_id("RULE-", (index - 1) * 5 + 2, 6),
                "requisition_id": requisition_id,
                "skill_id": chosen_skills[1],
                "rule_type": ScreeningRuleType.WEIGHTED_SKILL,
                "criterion_name": f"Weighted: {SKILLS[skill_ids.index(chosen_skills[1])][0]}",
                "weight": Decimal("25.00"),
                "minimum_proficiency": 2,
                "minimum_value": None,
                "effective_from": opened_at,
                "effective_to": None,
                "is_active": True,
            },
            {
                "screening_rule_id": _next_id("RULE-", (index - 1) * 5 + 3, 6),
                "requisition_id": requisition_id,
                "skill_id": chosen_skills[2],
                "rule_type": ScreeningRuleType.WEIGHTED_SKILL,
                "criterion_name": f"Weighted: {SKILLS[skill_ids.index(chosen_skills[2])][0]}",
                "weight": Decimal("25.00"),
                "minimum_proficiency": 2,
                "minimum_value": None,
                "effective_from": opened_at,
                "effective_to": None,
                "is_active": True,
            },
            {
                "screening_rule_id": _next_id("RULE-", (index - 1) * 5 + 4, 6),
                "requisition_id": requisition_id,
                "skill_id": None,
                "rule_type": ScreeningRuleType.MIN_EXPERIENCE,
                "criterion_name": "Minimum relevant experience",
                "weight": Decimal("20.00"),
                "minimum_proficiency": None,
                "minimum_value": Decimal("2.00"),
                "effective_from": opened_at,
                "effective_to": None,
                "is_active": True,
            },
            {
                "screening_rule_id": _next_id("RULE-", (index - 1) * 5 + 5, 6),
                "requisition_id": requisition_id,
                "skill_id": None,
                "rule_type": ScreeningRuleType.PASS_THRESHOLD,
                "criterion_name": "Weighted screening pass threshold",
                "weight": Decimal("0.00"),
                "minimum_proficiency": None,
                "minimum_value": Decimal("55.00"),
                "effective_from": opened_at,
                "effective_to": None,
                "is_active": True,
            },
        ]
        rules_by_requisition[requisition_id] = rules
        rows["screening_rules"].extend(rules)
        rows["requisition_events"].extend(
            [
                {
                    "event_id": _next_id("REQEV-", (index - 1) * 3 + 1, 6),
                    "requisition_id": requisition_id,
                    "from_status": None,
                    "to_status": RequisitionStatus.DRAFT,
                    "actor": requisition["created_by"],
                    "reason": "Requisition created",
                    "occurred_at": created_at,
                },
                {
                    "event_id": _next_id("REQEV-", (index - 1) * 3 + 2, 6),
                    "requisition_id": requisition_id,
                    "from_status": RequisitionStatus.DRAFT,
                    "to_status": RequisitionStatus.PENDING_APPROVAL,
                    "actor": requisition["created_by"],
                    "reason": "Submitted for approval",
                    "occurred_at": approved_at - timedelta(hours=1),
                },
                {
                    "event_id": _next_id("REQEV-", (index - 1) * 3 + 3, 6),
                    "requisition_id": requisition_id,
                    "from_status": RequisitionStatus.PENDING_APPROVAL,
                    "to_status": RequisitionStatus.OPEN,
                    "actor": requisition["approved_by"],
                    "reason": "Approved and opened",
                    "occurred_at": opened_at,
                },
            ]
        )

    candidate_skill_map: dict[str, dict[str, tuple[int, Decimal]]] = {}
    candidate_by_id: dict[str, dict[str, Any]] = {}
    for index in range(1, size["candidates"] + 1):
        candidate_id = f"CAND-{index:06d}"
        full_name = f"{FIRST_NAMES[(index - 1) % len(FIRST_NAMES)]} {LAST_NAMES[((index - 1) // len(FIRST_NAMES)) % len(LAST_NAMES)]}"
        selected_skills = rng.sample(skill_ids, rng.randint(10, 16))
        created_at = BASE_TIME + timedelta(minutes=index)
        candidate = {
            "candidate_id": candidate_id,
            "full_name": full_name,
            "email": f"candidate.{index:06d}@example.test",
            "phone": f"+1-555-{1000 + (index % 9000):04d}",
            "location": LOCATIONS[(index - 1) % len(LOCATIONS)],
            "professional_summary": (
                "Synthetic professional profile with experience in "
                + ", ".join(SKILLS[skill_ids.index(skill_id)][0] for skill_id in selected_skills[:4])
                + ". Interested in evidence-based, collaborative technical work."
            ),
            "portfolio_url": f"https://portfolio.example.test/candidate-{index:06d}",
            "created_at": created_at,
        }
        rows["candidates"].append(candidate)
        candidate_by_id[candidate_id] = candidate
        skill_map: dict[str, tuple[int, Decimal]] = {}
        for skill_id in selected_skills:
            proficiency = rng.randint(1, 5)
            years = _decimal(rng.randint(0, 12) + rng.choice((0, 0.25, 0.5, 0.75)))
            skill_map[skill_id] = (proficiency, years)
            rows["candidate_skills"].append(
                {
                    "candidate_id": candidate_id,
                    "skill_id": skill_id,
                    "effective_from": created_at + timedelta(days=1),
                    "proficiency_level": proficiency,
                    "years_experience": years,
                    "effective_to": None,
                }
            )
        candidate_skill_map[candidate_id] = skill_map
        resume_text = candidate["professional_summary"]
        rows["resume_artifacts"].append(
            {
                "resume_id": _next_id("RESUME-", index, 8),
                "candidate_id": candidate_id,
                "application_id": None,
                "file_name": f"candidate-{index:06d}-resume.pdf",
                "storage_uri": f"synthetic://resumes/{candidate_id}.pdf",
                "content_type": "application/pdf",
                "sha256": hashlib.sha256(f"{candidate_id}|synthetic-resume-v1".encode("utf-8")).hexdigest(),
                "extraction_status": ResumeStatus.PROCESSED,
                "extracted_text": resume_text,
                "uploaded_at": created_at + timedelta(hours=1),
            }
        )

    interviewer_count = max(10, size["interviews"] // 12)
    interviewer_ids: list[str] = []
    for index in range(1, interviewer_count + 1):
        interviewer_id = _next_id("ITR-", index, 6)
        interviewer_ids.append(interviewer_id)
        rows["interviewers"].append(
            {
                "interviewer_id": interviewer_id,
                "department_id": department_ids[(index - 1) % len(department_ids)],
                "display_name": f"Interviewer {index:03d}",
                "email": f"interviewer.{index:03d}@example.test",
                "is_active": True,
            }
        )
        rows["interviewer_availability"].append(
            {
                "availability_id": _next_id("AVAIL-", index, 6),
                "interviewer_id": interviewer_id,
                "starts_at": BASE_TIME,
                "ends_at": AVAILABILITY_END,
                "is_available": True,
            }
        )

    all_pairs = len(requisition_by_id) * len(candidate_by_id)
    if size["applications"] > all_pairs:
        raise GenerationError("Requested applications exceed unique candidate/requisition pairs.")
    sampled_pairs = rng.sample(range(all_pairs), size["applications"])
    app_records: list[dict[str, Any]] = []
    app_scores: dict[str, Decimal] = {}
    qualified_applications: list[dict[str, Any]] = []
    requisition_ids = list(requisition_by_id)
    candidate_ids = list(candidate_by_id)
    for index, pair_index in enumerate(sampled_pairs, start=1):
        requisition_id = requisition_ids[pair_index // len(candidate_ids)]
        candidate_id = candidate_ids[pair_index % len(candidate_ids)]
        application_id = f"APP-{requisition_id}-{candidate_id}"
        requisition = requisition_by_id[requisition_id]
        submitted_at = BASE_TIME + timedelta(days=10, minutes=index)
        rules = rules_by_requisition[requisition_id]
        score, required_met, components = _score_candidate(candidate_skill_map[candidate_id], rules)
        pass_threshold = next(
            (
                rule["minimum_value"]
                for rule in rules
                if rule["rule_type"] == ScreeningRuleType.PASS_THRESHOLD
            ),
            Decimal("55.00"),
        )
        explanation = {
            "criteria": components,
            "required_skills_met": required_met,
            "pass_threshold": format(pass_threshold, "f"),
            "scoring_method": "weighted mean of active job-related criteria",
            "total_relevant_experience_years": format(
                max(
                    (
                        candidate_skill_map[candidate_id].get(rule["skill_id"], (0, Decimal("0")))[1]
                        for rule in rules
                        if rule["skill_id"] is not None
                    ),
                    default=Decimal("0"),
                ),
                "f",
            ),
        }
        application = {
            "application_id": application_id,
            "requisition_id": requisition_id,
            "candidate_id": candidate_id,
            "status": ApplicationStatus.SCREENING,
            "submitted_at": submitted_at,
            "screening_score": score,
            "screening_explanation": _json_value(explanation),
            "source": SOURCES[(index - 1) % len(SOURCES)],
            "updated_at": submitted_at,
        }
        rows["applications"].append(application)
        app_records.append(application)
        app_scores[application_id] = score
        rows["application_events"].extend(
            [
                {
                    "event_id": _next_id("APPEV-", (index - 1) * 3 + 1, 8),
                    "application_id": application_id,
                    "from_status": None,
                    "to_status": ApplicationStatus.APPLIED,
                    "actor": "synthetic.candidate@example.test",
                    "reason": "Application submitted",
                    "occurred_at": submitted_at,
                },
                {
                    "event_id": _next_id("APPEV-", (index - 1) * 3 + 2, 8),
                    "application_id": application_id,
                    "from_status": ApplicationStatus.APPLIED,
                    "to_status": ApplicationStatus.SCREENING,
                    "actor": "synthetic.recruiting@example.test",
                    "reason": "Job-related screening started",
                    "occurred_at": submitted_at + timedelta(hours=1),
                },
            ]
        )
        if required_met and score >= pass_threshold:
            qualified_applications.append(application)
        else:
            application["status"] = ApplicationStatus.REJECTED
            rejected_at = submitted_at + timedelta(days=1)
            application["updated_at"] = rejected_at
            rows["application_events"].append(
                {
                    "event_id": _next_id("APPEV-", (index - 1) * 3 + 3, 8),
                    "application_id": application_id,
                    "from_status": ApplicationStatus.SCREENING,
                    "to_status": ApplicationStatus.REJECTED,
                    "actor": "synthetic.recruiting@example.test",
                    "reason": "Did not meet required job-related screening criteria",
                    "occurred_at": rejected_at,
                }
            )

    application_index_by_id = {
        row["application_id"]: index for index, row in enumerate(app_records, start=1)
    }
    interview_app_count = size["interviews"] // 2
    if size["interviews"] % 2:
        raise GenerationError("Interview target must be even to generate two rounds per application.")
    if len(qualified_applications) < interview_app_count:
        raise GenerationError(
            f"Only {len(qualified_applications)} applications met screening criteria; "
            f"{interview_app_count} are required for the interview target."
        )
    qualified_applications.sort(
        key=lambda app: (-app_scores[app["application_id"]], app["application_id"])
    )
    selected_applications = qualified_applications[:interview_app_count]
    interview_rows_by_app: dict[str, list[dict[str, Any]]] = defaultdict(list)
    feedback_rows_by_interview: dict[str, list[dict[str, Any]]] = defaultdict(list)
    interview_sequence = 0

    for application_index, application in enumerate(selected_applications):
        application_id = application["application_id"]
        score = app_scores[application_id]
        candidate_skills = candidate_skill_map[application["candidate_id"]]
        app_interview_rows: list[dict[str, Any]] = []
        for round_number in (1, 2):
            interview_sequence += 1
            interview_id = f"INT-2026-{interview_sequence:06d}"
            slot_day = (interview_sequence - 1) // 4
            slot_hour = 9 + ((interview_sequence - 1) % 4) * 2
            starts_at = datetime(2026, 1, 15, slot_hour, 0, tzinfo=UTC) + timedelta(days=slot_day)
            ends_at = starts_at + timedelta(hours=1)
            panel_start = (application_index * 2 + round_number - 1) % interviewer_count
            panel_ids = [
                interviewer_ids[panel_start],
                interviewer_ids[(panel_start + 1) % interviewer_count],
            ]
            interview = {
                "interview_id": interview_id,
                "application_id": application_id,
                "round_number": round_number,
                "interview_type": InterviewType.TECHNICAL if round_number == 1 else InterviewType.PANEL,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "status": InterviewStatus.COMPLETED,
                "outcome": None,
                "issue_type": None,
            }
            rows["interviews"].append(interview)
            app_interview_rows.append(interview)
            for interviewer_id in panel_ids:
                rows["interview_panel"].append(
                    {"interview_id": interview_id, "interviewer_id": interviewer_id}
                )
                technical = min(5, 3 + int(score >= 65) + int(score >= 85))
                communication = min(
                    5,
                    3 + int(len(candidate_skills) >= 13) + int(score >= 85),
                )
                role = min(5, 3 + int(score >= 65) + int(score >= 85))
                average = Decimal(technical + communication + role) / Decimal(3)
                if average >= Decimal("4.5"):
                    recommendation = Recommendation.STRONG_YES
                elif average >= Decimal("3.5"):
                    recommendation = Recommendation.YES
                elif average >= Decimal("2.5"):
                    recommendation = Recommendation.NO
                else:
                    recommendation = Recommendation.STRONG_NO
                feedback = {
                    "feedback_id": f"FB-{interview_id}-{interviewer_id}",
                    "interview_id": interview_id,
                    "interviewer_id": interviewer_id,
                    "technical_rating": technical,
                    "communication_rating": communication,
                    "role_rating": role,
                    "recommendation": recommendation,
                    "comments": (
                        "Ratings reflect role-related technical evidence, communication, "
                        "and alignment with the documented job criteria."
                    ),
                    "submitted_at": ends_at + timedelta(hours=1),
                }
                rows["feedback"].append(feedback)
                feedback_rows_by_interview[interview_id].append(feedback)
            average_recommendation = sum(
                Decimal(
                    feedback["technical_rating"]
                    + feedback["communication_rating"]
                    + feedback["role_rating"]
                )
                / Decimal(3)
                for feedback in feedback_rows_by_interview[interview_id]
            ) / Decimal(len(panel_ids))
            interview["outcome"] = (
                InterviewOutcome.ADVANCE
                if average_recommendation >= Decimal("3.5")
                else InterviewOutcome.HOLD
                if average_recommendation >= Decimal("2.5")
                else InterviewOutcome.REJECT
            )
        interview_rows_by_app[application_id] = app_interview_rows
        first_interview = app_interview_rows[0]
        application["status"] = ApplicationStatus.INTERVIEW
        application["updated_at"] = first_interview["starts_at"]
        app_index = application_index_by_id[application_id]
        rows["application_events"].append(
            {
                "event_id": _next_id("APPEV-", (app_index - 1) * 3 + 3, 8),
                "application_id": application_id,
                "from_status": ApplicationStatus.SCREENING,
                "to_status": ApplicationStatus.INTERVIEW,
                "actor": "synthetic.recruiting@example.test",
                "reason": "Screening criteria met; interview scheduled",
                "occurred_at": first_interview["starts_at"],
            }
        )

    offer_eligible = []
    for application in selected_applications:
        interviews = interview_rows_by_app[application["application_id"]]
        all_advance = all(
            interview["outcome"] == InterviewOutcome.ADVANCE for interview in interviews
        )
        all_recommended = all(
            feedback["recommendation"] in {Recommendation.YES, Recommendation.STRONG_YES}
            for interview in interviews
            for feedback in feedback_rows_by_interview[interview["interview_id"]]
        )
        if all_advance and all_recommended:
            offer_eligible.append(application)
    offer_eligible.sort(
        key=lambda app: (-app_scores[app["application_id"]], app["application_id"])
    )
    if len(offer_eligible) < size["offers"]:
        raise GenerationError(
            f"Only {len(offer_eligible)} applications passed interview and offer approvals; "
            f"{size['offers']} offers are required."
        )
    offer_applications = offer_eligible[: size["offers"]]
    for offer_index, application in enumerate(offer_applications, start=1):
        requisition = requisition_by_id[application["requisition_id"]]
        score = app_scores[application["application_id"]]
        salary_range = requisition["salary_max"] - requisition["salary_min"]
        salary = requisition["salary_min"] + _decimal(
            salary_range * score / Decimal("100")
        )
        salary = min(requisition["salary_max"], max(requisition["salary_min"], salary))
        round_two = interview_rows_by_app[application["application_id"]][1]
        submitted_at = round_two["ends_at"] + timedelta(days=1)
        approved_at = submitted_at + timedelta(hours=1)
        issued_at = approved_at + timedelta(hours=1)
        responded_at = issued_at + timedelta(days=7)
        panel_feedback = [
            feedback
            for interview in interview_rows_by_app[application["application_id"]]
            for feedback in feedback_rows_by_interview[interview["interview_id"]]
        ]
        strong_panel = all(
            feedback["recommendation"] == Recommendation.STRONG_YES
            for feedback in panel_feedback
        )
        salary_fraction = (
            (salary - requisition["salary_min"])
            / (requisition["salary_max"] - requisition["salary_min"])
        )
        accepted = salary_fraction >= Decimal("0.90") and strong_panel
        final_offer_status = OfferStatus.ACCEPTED if accepted else OfferStatus.REJECTED
        final_application_status = (
            ApplicationStatus.HIRED if accepted else ApplicationStatus.REJECTED
        )
        response_reason = (
            "Accepted: salary reached at least 90% of the approved salary range and all "
            "panelists submitted strong_yes."
            if accepted
            else "Rejected: the salary-range threshold or unanimous strong interview "
            "recommendation was not met."
        )
        offer_id = f"OFF-2026-{offer_index:06d}"
        rows["offers"].append(
            {
                "offer_id": offer_id,
                "application_id": application["application_id"],
                "salary": salary,
                "currency": requisition["currency"],
                "status": final_offer_status,
                "created_by": "synthetic.recruiting@example.test",
                "approved_by": "synthetic.finance@example.test",
                "approved_at": approved_at,
                "issued_at": issued_at,
                "responded_at": responded_at,
            }
        )
        offer_event_id = (offer_index - 1) * 4
        rows["offer_events"].extend(
            [
                {
                    "event_id": _next_id("OFFEV-", offer_event_id + 1, 6),
                    "offer_id": offer_id,
                    "from_status": None,
                    "to_status": OfferStatus.PENDING_APPROVAL,
                    "actor": "synthetic.recruiting@example.test",
                    "reason": "Offer submitted for approval after positive interviews",
                    "occurred_at": submitted_at,
                },
                {
                    "event_id": _next_id("OFFEV-", offer_event_id + 2, 6),
                    "offer_id": offer_id,
                    "from_status": OfferStatus.PENDING_APPROVAL,
                    "to_status": OfferStatus.APPROVED,
                    "actor": "synthetic.finance@example.test",
                    "reason": "Salary is within approved requisition band",
                    "occurred_at": approved_at,
                },
                {
                    "event_id": _next_id("OFFEV-", offer_event_id + 3, 6),
                    "offer_id": offer_id,
                    "from_status": OfferStatus.APPROVED,
                    "to_status": OfferStatus.ISSUED,
                    "actor": "synthetic.recruiting@example.test",
                    "reason": "Approved offer issued",
                    "occurred_at": issued_at,
                },
                {
                    "event_id": _next_id("OFFEV-", offer_event_id + 4, 6),
                    "offer_id": offer_id,
                    "from_status": OfferStatus.ISSUED,
                    "to_status": final_offer_status,
                    "actor": "synthetic.candidate@example.test",
                    "reason": response_reason,
                    "occurred_at": responded_at,
                },
            ]
        )
        rows["application_events"].append(
            {
                "event_id": _next_id("APPEV-", offer_index + size["applications"] * 3, 8),
                "application_id": application["application_id"],
                "from_status": ApplicationStatus.INTERVIEW,
                "to_status": ApplicationStatus.OFFER,
                "actor": "synthetic.recruiting@example.test",
                "reason": "All interview panelists recommended advancing",
                "occurred_at": issued_at,
            }
        )
        rows["application_events"].append(
            {
                "event_id": _next_id("APPEV-", offer_index + size["applications"] * 4, 8),
                "application_id": application["application_id"],
                "from_status": ApplicationStatus.OFFER,
                "to_status": final_application_status,
                "actor": "synthetic.candidate@example.test",
                "reason": response_reason,
                "occurred_at": responded_at,
            }
        )
        application["status"] = final_application_status
        application["updated_at"] = responded_at

    relevant_entities = (
        ("Department", rows["departments"], "department_id", "created_at"),
        ("Skill", rows["skills"], "skill_id", None),
        ("JobRequisition", rows["job_requisitions"], "requisition_id", "created_at"),
        ("Candidate", rows["candidates"], "candidate_id", "created_at"),
        ("Application", rows["applications"], "application_id", "submitted_at"),
        ("Interview", rows["interviews"], "interview_id", "starts_at"),
        ("Offer", rows["offers"], "offer_id", "approved_at"),
    )
    audit_number = 0
    for entity_type, entities, id_column, timestamp_column in relevant_entities:
        for entity in entities:
            audit_number += 1
            rows["audit_events"].append(
                {
                    "audit_event_id": _next_id("AUDIT-", audit_number, 8),
                    "entity_type": entity_type,
                    "entity_id": entity[id_column],
                    "action": "synthetic_create",
                    "actor": "synthetic.generator@example.test",
                    "changes": {"seed": SEED, "source": "synthetic"},
                    "occurred_at": entity.get(timestamp_column, BASE_TIME)
                    if timestamp_column
                    else BASE_TIME,
                }
            )

    return rows

def _insert_baseline(session: Session, rows: dict[str, list[dict[str, Any]]]) -> None:
    insertion_order = (
        "departments",
        "skills",
        "job_requisitions",
        "candidates",
        "candidate_skills",
        "resume_artifacts",
        "screening_rules",
        "interviewers",
        "interviewer_availability",
        "applications",
        "interviews",
        "interview_panel",
        "feedback",
        "offers",
        "requisition_events",
        "application_events",
        "offer_events",
        "duplicate_application_attempts",
        "audit_events",
        "exception_records",
    )
    for table_name in insertion_order:
        if not rows[table_name]:
            continue
        table = Base.metadata.tables[table_name]
        session.execute(insert(table), rows[table_name])

def _inject_anomalies(session: Session) -> list[dict[str, str]]:
    applications = list(
        session.scalars(select(models.Application).order_by(models.Application.application_id)).all()
    )
    interviews = list(
        session.scalars(select(models.Interview).order_by(models.Interview.interview_id)).all()
    )
    offers = list(session.scalars(select(models.Offer).order_by(models.Offer.offer_id)).all())
    requisitions = list(
        session.scalars(
            select(models.JobRequisition).order_by(models.JobRequisition.requisition_id)
        ).all()
    )
    if not applications or not interviews or not offers:
        raise GenerationError("Anomaly injection requires baseline applications, interviews, and offers.")

    ground_truth: list[dict[str, str]] = []

    def add_anomaly(
        number: int,
        entity_type: str,
        entity_id: str,
        issue_type: ExceptionType,
        details: dict[str, Any],
    ) -> None:
        anomaly_id = f"ANOM-{number:03d}"
        issue_value = issue_type.value
        severity = SEVERITY_BY_ISSUE[issue_value]
        ground_truth.append(
            {
                "anomaly_id": anomaly_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "issue_type": issue_value,
                "expected_detection_result": "detected",
                "expected_severity": severity,
            }
        )
        session.add(
            models.ExceptionRecord(
                exception_id=f"EXC-{number:06d}",
                issue_type=issue_type,
                entity_type=entity_type,
                entity_id=entity_id,
                expected_decision="flag",
                observed_details=details,
                validation_result="not_evaluated",
                detected_at=BASE_TIME + timedelta(days=300, minutes=number),
            )
        )
        session.add(
            models.AuditEvent(
                audit_event_id=f"AUDIT-ANOM-{number:03d}",
                entity_type=entity_type,
                entity_id=entity_id,
                action="synthetic_anomaly_injected",
                actor="synthetic.generator@example.test",
                changes={"anomaly_id": anomaly_id, "issue_type": issue_value},
                occurred_at=BASE_TIME + timedelta(days=300, minutes=number),
            )
        )

    unavailable_interview = interviews[0]
    panelist_id = session.execute(
        select(models.interview_panel.c.interviewer_id)
        .where(models.interview_panel.c.interview_id == unavailable_interview.interview_id)
        .order_by(models.interview_panel.c.interviewer_id)
        .limit(1)
    ).scalar_one()
    unavailable_interview.issue_type = ExceptionType.UNAVAILABLE_INTERVIEWER
    session.add(
        models.InterviewerAvailability(
            availability_id="AVAIL-ANOM-001",
            interviewer_id=panelist_id,
            starts_at=unavailable_interview.starts_at,
            ends_at=unavailable_interview.ends_at,
            is_available=False,
        )
    )
    add_anomaly(
        1,
        "Interview",
        unavailable_interview.interview_id,
        ExceptionType.UNAVAILABLE_INTERVIEWER,
        {"interviewer_id": panelist_id, "scheduled_interval_is_unavailable": True},
    )

    original_application = applications[0]
    duplicate = models.DuplicateApplicationAttempt(
        attempt_id="DUP-ATT-000001",
        requisition_id=original_application.requisition_id,
        candidate_id=original_application.candidate_id,
        original_application_id=original_application.application_id,
        attempted_at=original_application.submitted_at + timedelta(days=1),
        issue_type=ExceptionType.DUPLICATE_APPLICATION,
    )
    session.add(duplicate)
    add_anomaly(
        2,
        "DuplicateApplicationAttempt",
        duplicate.attempt_id,
        ExceptionType.DUPLICATE_APPLICATION,
        {"original_application_id": original_application.application_id},
    )

    interview_counts = defaultdict(int)
    for interview in interviews:
        interview_counts[interview.application_id] += 1
    feedback_anomaly_interview = next(
        interview
        for interview in reversed(interviews)
        if interview_counts[interview.application_id] == 2
        and not any(offer.application_id == interview.application_id for offer in offers)
        and interview.round_number == 2
    )
    missing_feedback = session.scalar(
        select(models.Feedback)
        .where(models.Feedback.interview_id == feedback_anomaly_interview.interview_id)
        .order_by(models.Feedback.interviewer_id)
    )
    if missing_feedback is None:
        raise GenerationError("Could not select feedback for the missing-feedback anomaly.")
    session.delete(missing_feedback)
    add_anomaly(
        3,
        "Interview",
        feedback_anomaly_interview.interview_id,
        ExceptionType.MISSING_FEEDBACK_ADVANCEMENT,
        {
            "application_id": feedback_anomaly_interview.application_id,
            "application_stage": "interview",
            "missing_interviewer_id": missing_feedback.interviewer_id,
        },
    )

    salary_offer = offers[0]
    salary_application = session.get(models.Application, salary_offer.application_id)
    salary_requisition = session.get(models.JobRequisition, salary_application.requisition_id)
    salary_offer.salary = salary_requisition.salary_max + Decimal("1000.00")
    add_anomaly(
        4,
        "Offer",
        salary_offer.offer_id,
        ExceptionType.OFFER_OUTSIDE_SALARY_BAND,
        {
            "approved_salary_max": format(salary_requisition.salary_max, "f"),
            "offer_salary": format(salary_offer.salary, "f"),
        },
    )

    active_statuses = {
        ApplicationStatus.APPLIED,
        ApplicationStatus.SCREENING,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.OFFER,
    }
    apps_by_requisition: dict[str, list[models.Application]] = defaultdict(list)
    for application in applications:
        apps_by_requisition[application.requisition_id].append(application)
    closed_requisition = next(
        (
            requisition
            for requisition in requisitions
            if any(app.status in active_statuses for app in apps_by_requisition[requisition.requisition_id])
        ),
        None,
    )
    if closed_requisition is None:
        raise GenerationError("Could not select a requisition with active applications.")
    closed_requisition.status = RequisitionStatus.CLOSED
    closed_requisition.closed_at = ANOMALY_CLOSE_TIME
    session.add(
        models.RequisitionEvent(
            event_id="REQEV-ANOM-001",
            requisition_id=closed_requisition.requisition_id,
            from_status=RequisitionStatus.OPEN,
            to_status=RequisitionStatus.CLOSED,
            actor="synthetic.recruiting@example.test",
            reason="Deliberate closed-requisition anomaly case",
            occurred_at=ANOMALY_CLOSE_TIME,
        )
    )
    add_anomaly(
        5,
        "JobRequisition",
        closed_requisition.requisition_id,
        ExceptionType.CLOSED_REQUISITION_ACTIVE_APPLICATIONS,
        {
            "active_application_count": sum(
                app.status in active_statuses
                for app in apps_by_requisition[closed_requisition.requisition_id]
            )
        },
    )
    return ground_truth

def _enable_sqlite_foreign_keys(engine: Any) -> None:
    @event.listens_for(engine, "connect")
    def _set_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
        _ = connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

def _export_session(
    session: Session,
    size_name: str,
    ground_truth: list[dict[str, str]],
) -> tuple[dict[str, bytes], dict[str, int], dict[str, Any]]:
    tables = sorted(Base.metadata.tables.values(), key=lambda table: table.name)
    dataset: dict[str, list[dict[str, Any]]] = {}
    csv_exports: dict[str, bytes] = {}
    sql_lines = [
        f"-- Deterministic synthetic {size_name} seed data; seed={SEED}.",
        "-- Apply after the existing Recruitment Management System schema is installed.",
        "",
        "BEGIN;",
        "",
    ]
    entity_counts: dict[str, int] = {}
    for table in tables:
        statement = select(table).order_by(*table.primary_key.columns)
        records = [dict(row) for row in session.execute(statement).mappings().all()]
        dataset[table.name] = records
        entity_counts[table.name] = len(records)

        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        column_names = [column.name for column in table.columns]
        writer.writerow(column_names)
        for record in records:
            writer.writerow([_csv_value(record[column]) for column in column_names])
        csv_exports[f"{size_name}_{table.name}.csv"] = output.getvalue().encode("utf-8")

    ordered_table_names = (
        "departments",
        "skills",
        "job_requisitions",
        "candidates",
        "candidate_skills",
        "resume_artifacts",
        "screening_rules",
        "interviewers",
        "interviewer_availability",
        "applications",
        "interviews",
        "interview_panel",
        "feedback",
        "offers",
        "requisition_events",
        "application_events",
        "offer_events",
        "duplicate_application_attempts",
        "audit_events",
        "exception_records",
    )
    for table_name in ordered_table_names:
        table = Base.metadata.tables[table_name]
        column_names = [column.name for column in table.columns]
        records = dataset[table_name]
        for record in records:
            values = ", ".join(_sql_literal(record[column]) for column in column_names)
            quoted_columns = ", ".join(f'"{column}"' for column in column_names)
            sql_lines.append(
                f'INSERT INTO "{table_name}" ({quoted_columns}) VALUES ({values});'
            )
    sql_lines.extend(("", "COMMIT;"))
    sql_exports = {f"{size_name}_seed.sql": ("\n".join(sql_lines) + "\n").encode("utf-8")}

    truth_columns = (
        "anomaly_id",
        "entity_type",
        "entity_id",
        "issue_type",
        "expected_detection_result",
        "expected_severity",
    )
    truth_output = io.StringIO(newline="")
    truth_writer = csv.DictWriter(
        truth_output, fieldnames=truth_columns, lineterminator="\n"
    )
    truth_writer.writeheader()
    truth_writer.writerows(ground_truth)

    report = validate_dataset(session)
    validation_summary = compare_ground_truth(report, ground_truth)
    validation_summary["offer_status_distribution"] = dict(
        sorted(
            Counter(_csv_value(row["status"]) for row in dataset["offers"]).items()
        )
    )
    exports: dict[str, bytes] = {
        **csv_exports,
        **sql_exports,
        f"{size_name}_data.json": _stable_json(dataset),
        f"{size_name}_ground_truth.csv": truth_output.getvalue().encode("utf-8"),
        f"{size_name}_ground_truth.json": _stable_json(ground_truth),
        f"{size_name}_validation.json": _stable_json(validation_summary),
    }
    return exports, entity_counts, validation_summary

def _generate_once(size_name: str) -> tuple[dict[str, bytes], dict[str, int], dict[str, Any], list[dict[str, str]]]:
    rows = _generate_baseline(size_name)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    ground_truth: list[dict[str, str]] = []
    try:
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                _insert_baseline(session, rows)
            with session.begin():
                ground_truth = _inject_anomalies(session)
            exports, entity_counts, validation_summary = _export_session(
                session, size_name, ground_truth
            )
    finally:
        engine.dispose()
    return exports, entity_counts, validation_summary, ground_truth

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _offer_status_distribution(export: bytes) -> dict[str, int]:
    reader = csv.DictReader(io.StringIO(export.decode("utf-8")))
    return dict(sorted(Counter(row["status"] for row in reader).items()))

def _reproducibility_manifest(
    first_exports: dict[str, bytes],
    second_exports: dict[str, bytes],
    first_counts: dict[str, int],
    second_counts: dict[str, int],
    first_ground_truth: list[dict[str, str]],
    second_ground_truth: list[dict[str, str]],
) -> dict[str, Any]:
    csv_files = sorted(name for name in first_exports if name.endswith(".csv"))
    json_files = sorted(name for name in first_exports if name.endswith(".json"))
    sql_files = sorted(name for name in first_exports if name.endswith(".sql"))
    offer_files = [name for name in first_exports if name.endswith("_offers.csv")]
    if len(offer_files) != 1:
        raise GenerationError("Expected exactly one offers CSV export for reproducibility checks.")
    offer_status_distribution = _offer_status_distribution(first_exports[offer_files[0]])
    second_offer_status_distribution = _offer_status_distribution(second_exports[offer_files[0]])
    csv_checksums = {name: _sha256(first_exports[name]) for name in csv_files}
    json_checksums = {name: _sha256(first_exports[name]) for name in json_files}
    sql_checksums = {name: _sha256(first_exports[name]) for name in sql_files}
    files_match = first_exports == second_exports
    counts_match = first_counts == second_counts
    ground_truth_match = first_ground_truth == second_ground_truth
    status_distribution_match = (
        offer_status_distribution == second_offer_status_distribution
    )
    return {
        "seed": SEED,
        "runs_compared": 2,
        "runs_identical": (
            files_match
            and counts_match
            and ground_truth_match
            and status_distribution_match
        ),
        "entity_counts_match": counts_match,
        "entity_counts": first_counts,
        "offer_status_distribution": offer_status_distribution,
        "offer_status_distribution_match": status_distribution_match,
        "csv_checksums": csv_checksums,
        "json_checksums": json_checksums,
        "sql_checksums": sql_checksums,
        "ground_truth_count": len(first_ground_truth),
        "ground_truth_counts_match": (
            len(first_ground_truth) == len(second_ground_truth) and ground_truth_match
        ),
    }

def _write_exports(exports: dict[str, bytes], manifest: dict[str, Any] | None) -> None:
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for filename, contents in sorted(exports.items()):
        (OUTPUT_DIRECTORY / filename).write_bytes(contents)
    if manifest is not None:
        (OUTPUT_DIRECTORY / f"{manifest['dataset_size']}_reproducibility.json").write_bytes(
            _stable_json(manifest)
        )

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", choices=sorted(DATASET_SIZES), default="full")
    parser.add_argument(
        "--verify-reproducibility",
        action="store_true",
        help="Generate the same dataset twice and compare row counts and export checksums.",
    )
    args = parser.parse_args()

    exports, counts, validation_summary, ground_truth = _generate_once(args.size)
    manifest = None
    if args.verify_reproducibility:
        second_exports, second_counts, _, second_ground_truth = _generate_once(args.size)
        manifest = _reproducibility_manifest(
            exports,
            second_exports,
            counts,
            second_counts,
            ground_truth,
            second_ground_truth,
        )
        manifest["dataset_size"] = args.size
        if not manifest["runs_identical"]:
            raise GenerationError("Reproducibility check failed; generated output differs between runs.")
    if not validation_summary["validation_passed"]:
        raise GenerationError(
            "Validation failed: "
            f"{validation_summary['undetected_known_anomalies']} known anomalies were missed and "
            f"{validation_summary['unexpected_anomalies']} unexpected anomalies were found."
        )
    _write_exports(exports, manifest)
    print(f"Dataset: {args.size}")
    print(f"Output directory: {OUTPUT_DIRECTORY}")
    print(f"Entity row counts: {json.dumps(counts, sort_keys=True)}")
    print(
        "Validation: "
        f"{validation_summary['passed_checks']}/{validation_summary['total_checks']} checks passed; "
        f"{validation_summary['detected_anomalies']} anomalies detected; "
        f"{validation_summary['undetected_known_anomalies']} undetected; "
        f"{validation_summary['unexpected_anomalies']} unexpected."
    )
    print(
        f"Offer status distribution: "
        f"{json.dumps(validation_summary['offer_status_distribution'], sort_keys=True)}"
    )
    if manifest is not None:
        print(
            "Reproducibility: "
            f"{'identical' if manifest['runs_identical'] else 'different'}; "
            f"compared {manifest['runs_compared']} runs."
        )

if __name__ == "__main__":
    main()
