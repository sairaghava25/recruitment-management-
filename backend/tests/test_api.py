from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, insert, select
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.models import (
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
from app.main import create_app
from app.services import calculate_screening_score
from app.data_tools import (
    _generate_baseline,
    _inject_anomalies,
    _insert_baseline,
    validate_dataset,
)


def test_health_explains_missing_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_not_configured"


@pytest.fixture
def api(tmp_path):
    database_path = tmp_path / "api-tests.sqlite"
    from sqlalchemy import create_engine

    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(factory)
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def _seed_root(factory, candidate_count: int = 1) -> tuple[str, list[str]]:
    now = datetime.now(timezone.utc)
    requisition_id = "REQ-2026-000001"
    candidate_ids = [f"CAND-{index:06d}" for index in range(1, candidate_count + 1)]
    with factory() as session:
        session.add_all(
            [
                models.Department(
                    department_id="DEPT-0001",
                    name="Engineering",
                    description="Software engineering.",
                    is_active=True,
                ),
                models.Skill(skill_id="SKILL-0001", name="Python", category="Programming"),
                models.Skill(skill_id="SKILL-0002", name="SQL", category="Data"),
                models.Skill(skill_id="SKILL-0003", name="Cloud", category="Platform"),
            ]
        )
        session.flush()
        requisition = models.JobRequisition(
            requisition_id=requisition_id,
            department_id="DEPT-0001",
            title="Backend Engineer",
            description="Build and maintain backend services.",
            employment_type=EmploymentType.FULL_TIME,
            location="Remote",
            salary_min=Decimal("80000"),
            salary_max=Decimal("120000"),
            currency="USD",
            headcount=1,
            status=RequisitionStatus.OPEN,
            created_by="test:recruiter",
            approved_by="test:approver",
            approved_at=now - timedelta(days=2),
            opened_at=now - timedelta(days=2),
            created_at=now - timedelta(days=3),
        )
        session.add(requisition)
        session.flush()
        session.add_all(
            [
                models.RequisitionEvent(
                    event_id=uuid4().hex,
                    requisition_id=requisition_id,
                    from_status=None,
                    to_status=RequisitionStatus.DRAFT,
                    actor="test:recruiter",
                    occurred_at=now - timedelta(days=3),
                ),
                models.RequisitionEvent(
                    event_id=uuid4().hex,
                    requisition_id=requisition_id,
                    from_status=RequisitionStatus.DRAFT,
                    to_status=RequisitionStatus.PENDING_APPROVAL,
                    actor="test:approver",
                    occurred_at=now - timedelta(days=2, minutes=1),
                ),
                models.RequisitionEvent(
                    event_id=uuid4().hex,
                    requisition_id=requisition_id,
                    from_status=RequisitionStatus.PENDING_APPROVAL,
                    to_status=RequisitionStatus.OPEN,
                    actor="test:approver",
                    occurred_at=now - timedelta(days=2),
                ),
            ]
        )
        session.add_all(
            [
                models.ScreeningRule(
                    screening_rule_id="RULE-000001",
                    requisition_id=requisition_id,
                    skill_id="SKILL-0001",
                    rule_type=ScreeningRuleType.REQUIRED_SKILL,
                    criterion_name="Python proficiency",
                    weight=Decimal("30"),
                    minimum_proficiency=2,
                    effective_from=now - timedelta(days=3),
                    is_active=True,
                ),
                models.ScreeningRule(
                    screening_rule_id="RULE-000002",
                    requisition_id=requisition_id,
                    skill_id="SKILL-0002",
                    rule_type=ScreeningRuleType.WEIGHTED_SKILL,
                    criterion_name="SQL proficiency",
                    weight=Decimal("30"),
                    minimum_proficiency=3,
                    effective_from=now - timedelta(days=3),
                    is_active=True,
                ),
                models.ScreeningRule(
                    screening_rule_id="RULE-000003",
                    requisition_id=requisition_id,
                    skill_id="SKILL-0003",
                    rule_type=ScreeningRuleType.WEIGHTED_SKILL,
                    criterion_name="Cloud proficiency",
                    weight=Decimal("20"),
                    minimum_proficiency=2,
                    effective_from=now - timedelta(days=3),
                    is_active=True,
                ),
                models.ScreeningRule(
                    screening_rule_id="RULE-000004",
                    requisition_id=requisition_id,
                    rule_type=ScreeningRuleType.MIN_EXPERIENCE,
                    criterion_name="Relevant experience",
                    weight=Decimal("20"),
                    minimum_value=Decimal("2"),
                    effective_from=now - timedelta(days=3),
                    is_active=True,
                ),
                models.ScreeningRule(
                    screening_rule_id="RULE-000005",
                    requisition_id=requisition_id,
                    rule_type=ScreeningRuleType.PASS_THRESHOLD,
                    criterion_name="Minimum screening score",
                    weight=Decimal("0"),
                    minimum_value=Decimal("55"),
                    effective_from=now - timedelta(days=3),
                    is_active=True,
                ),
            ]
        )
        for index, candidate_id in enumerate(candidate_ids, start=1):
            session.add(
                models.Candidate(
                    candidate_id=candidate_id,
                    full_name=f"Candidate {index}",
                    email=f"candidate{index}@example.test",
                    location="Remote",
                    professional_summary="Backend engineering experience.",
                )
            )
            session.flush()
            session.add_all(
                [
                    models.CandidateSkill(
                        candidate_id=candidate_id,
                        skill_id="SKILL-0001",
                        effective_from=now - timedelta(days=2),
                        proficiency_level=5,
                        years_experience=Decimal("4"),
                    ),
                    models.CandidateSkill(
                        candidate_id=candidate_id,
                        skill_id="SKILL-0002",
                        effective_from=now - timedelta(days=2),
                        proficiency_level=3,
                        years_experience=Decimal("3"),
                    ),
                ]
            )
        session.commit()
    return requisition_id, candidate_ids


def _seed_application(
    factory,
    requisition_id: str,
    candidate_id: str,
    status: ApplicationStatus = ApplicationStatus.APPLIED,
) -> str:
    now = datetime.now(timezone.utc)
    application_id = f"APP-{requisition_id}-{candidate_id}"
    events = [(None, ApplicationStatus.APPLIED)]
    if status in {ApplicationStatus.SCREENING, ApplicationStatus.INTERVIEW}:
        events.append((ApplicationStatus.APPLIED, ApplicationStatus.SCREENING))
    if status == ApplicationStatus.INTERVIEW:
        events.append((ApplicationStatus.SCREENING, ApplicationStatus.INTERVIEW))
    with factory() as session:
        application = models.Application(
            application_id=application_id,
            requisition_id=requisition_id,
            candidate_id=candidate_id,
            status=status,
            submitted_at=now - timedelta(days=1),
            screening_score=Decimal("68"),
            screening_explanation={"outcome": "pass"},
            source="test",
            updated_at=now,
        )
        session.add(application)
        for index, (previous, current) in enumerate(events):
            session.add(
                models.ApplicationEvent(
                    event_id=uuid4().hex,
                    application_id=application_id,
                    from_status=previous,
                    to_status=current,
                    actor="test:recruiter",
                    occurred_at=now - timedelta(hours=len(events) - index),
                )
            )
        session.commit()
    return application_id


def _seed_interview(
    factory,
    application_id: str,
    *,
    panel_size: int = 1,
    feedback_count: int | None = None,
    outcome: InterviewOutcome | None = InterviewOutcome.ADVANCE,
    status: InterviewStatus = InterviewStatus.COMPLETED,
) -> tuple[str, list[str]]:
    now = datetime.now(timezone.utc)
    interview_id = f"INT-2026-{uuid4().hex[:6]}"
    interviewer_ids = [f"INTV-{uuid4().hex[:8]}" for _ in range(panel_size)]
    start = now - timedelta(hours=3)
    end = now - timedelta(hours=2)
    with factory() as session:
        for index, interviewer_id in enumerate(interviewer_ids):
            session.add(
                models.Interviewer(
                    interviewer_id=interviewer_id,
                    display_name=f"Interviewer {index + 1}",
                    email=f"{interviewer_id.lower()}@example.test",
                    is_active=True,
                )
            )
        interview = models.Interview(
            interview_id=interview_id,
            application_id=application_id,
            round_number=1,
            interview_type=InterviewType.TECHNICAL,
            starts_at=start,
            ends_at=end,
            status=status,
            outcome=outcome,
        )
        session.add(interview)
        session.flush()
        session.execute(
            insert(models.interview_panel),
            [
                {"interview_id": interview_id, "interviewer_id": interviewer_id}
                for interviewer_id in interviewer_ids
            ],
        )
        if feedback_count is None:
            feedback_count = panel_size
        for index, interviewer_id in enumerate(interviewer_ids[:feedback_count]):
            session.add(
                models.Feedback(
                    feedback_id=uuid4().hex,
                    interview_id=interview_id,
                    interviewer_id=interviewer_id,
                    technical_rating=4,
                    communication_rating=4,
                    role_rating=4,
                    recommendation=Recommendation.YES,
                    comments="Meets the role criteria.",
                    submitted_at=end + timedelta(minutes=index + 1),
                )
            )
        session.commit()
    return interview_id, interviewer_ids


def _requisition_request(skill_id: str) -> dict:
    return {
        "department_id": "DEPT-0001",
        "title": "Data Engineer",
        "description": "Build data services.",
        "employment_type": "full_time",
        "location": "Remote",
        "salary_min": "90000",
        "salary_max": "130000",
        "currency": "USD",
        "headcount": 1,
        "created_by": "test:recruiter",
        "screening_rules": [
            {
                "skill_id": skill_id,
                "rule_type": "required_skill",
                "criterion_name": "Required skill",
                "weight": "100",
                "minimum_proficiency": 2,
            },
            {
                "rule_type": "pass_threshold",
                "criterion_name": "Pass threshold",
                "minimum_value": "55",
            },
        ],
    }


def test_requisition_approval_and_invalid_repeat(api):
    client, factory = api
    _seed_root(factory)
    response = client.post("/api/requisitions", json=_requisition_request("SKILL-0001"))
    assert response.status_code == 201
    requisition_id = response.json()["requisition_id"]
    patched = client.patch(
        f"/api/requisitions/{requisition_id}",
        json={"title": "Senior Data Engineer", "updated_by": "test:recruiter"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Senior Data Engineer"

    approved = client.post(
        f"/api/requisitions/{requisition_id}/approve",
        json={"actor": "test:approver"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "open"
    assert approved.json()["approved_at"] is not None
    closed = client.post(
        f"/api/requisitions/{requisition_id}/close",
        json={"actor": "test:recruiter", "reason": "Position filled."},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    repeated = client.post(
        f"/api/requisitions/{requisition_id}/approve",
        json={"actor": "test:approver"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "invalid_requisition_state"


def test_application_screening_interview_feedback_and_audit(api):
    client, factory = api
    requisition_id, candidates = _seed_root(factory)
    created = client.post(
        "/api/applications",
        json={"candidate_id": candidates[0], "requisition_id": requisition_id},
    )
    assert created.status_code == 201
    application_id = created.json()["application_id"]
    assert client.get(
        f"/api/applications/{application_id}/screening"
    ).json()["score"] == "68.00"
    screened = client.post(f"/api/applications/{application_id}/screen")
    assert screened.status_code == 200
    advanced = client.post(
        f"/api/applications/{application_id}/advance",
        json={"to_status": "interview", "actor": "test:recruiter"},
    )
    assert advanced.status_code == 200

    interviewer_ids = ["INTV-PANEL-1", "INTV-PANEL-2"]
    starts = datetime.now(timezone.utc) + timedelta(days=2)
    ends = starts + timedelta(hours=1)
    with factory() as session:
        for index, interviewer_id in enumerate(interviewer_ids, start=1):
            session.add(
                models.Interviewer(
                    interviewer_id=interviewer_id,
                    display_name=f"Panelist {index}",
                    email=f"panelist{index}@example.test",
                    is_active=True,
                )
            )
            session.add(
                models.InterviewerAvailability(
                    availability_id=f"AVAIL-PANEL-{index}",
                    interviewer_id=interviewer_id,
                    starts_at=starts - timedelta(hours=1),
                    ends_at=ends + timedelta(hours=3),
                    is_available=True,
                )
            )
        session.commit()

    scheduled = client.post(
        "/api/interviews",
        json={
            "application_id": application_id,
            "round_number": 1,
            "interview_type": "technical",
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            "interviewer_ids": interviewer_ids,
        },
    )
    assert scheduled.status_code == 201
    interview_id = scheduled.json()["interview_id"]
    rescheduled = client.patch(
        f"/api/interviews/{interview_id}/schedule",
        json={
            "starts_at": (starts + timedelta(hours=1)).isoformat(),
            "ends_at": (ends + timedelta(hours=1)).isoformat(),
            "interviewer_ids": interviewer_ids,
        },
    )
    assert rescheduled.status_code == 200
    not_finished = client.post(
        f"/api/interviews/{interview_id}/feedback",
        json={
            "interviewer_id": interviewer_ids[0],
            "technical_rating": 4,
            "communication_rating": 4,
            "role_rating": 4,
            "recommendation": "yes",
        },
    )
    assert not_finished.status_code == 409
    assert not_finished.json()["error"]["code"] == "interview_not_finished"

    with factory() as session:
        interview = session.get(models.Interview, interview_id)
        interview.starts_at = datetime.now(timezone.utc) - timedelta(hours=3)
        interview.ends_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.commit()
    for interviewer_id in interviewer_ids:
        feedback = client.post(
            f"/api/interviews/{interview_id}/feedback",
            json={
                "interviewer_id": interviewer_id,
                "technical_rating": 4,
                "communication_rating": 4,
                "role_rating": 4,
                "recommendation": "yes",
                "comments": "Meets the role criteria.",
            },
        )
        assert feedback.status_code == 201
    feedback_history = client.get(f"/api/interviews/{interview_id}/feedback")
    interview_detail = client.get(f"/api/interviews/{interview_id}")
    assert len(feedback_history.json()) == 2
    assert interview_detail.json()["status"] == "completed"
    assert interview_detail.json()["outcome"] == "advance"
    assert len(client.get("/api/interviews", params={"application_id": application_id}).json()) == 1
    assert len(client.get("/api/applications").json()) == 1
    assert client.get("/api/candidates").status_code == 200
    assert client.get(f"/api/candidates/{candidates[0]}").status_code == 200
    assert client.get("/api/requisitions").status_code == 200
    assert client.get(f"/api/requisitions/{requisition_id}").status_code == 200
    audit = client.get(
        "/api/audit",
        params={"entity_type": "interview", "entity_id": interview_id},
    )
    assert audit.status_code == 200
    assert len(audit.json()) >= 4
    assert client.get("/health").json() == {"status": "ok", "database": "connected"}


def test_duplicate_application_attempt_is_preserved(api):
    client, factory = api
    requisition_id, candidates = _seed_root(factory)
    payload = {"candidate_id": candidates[0], "requisition_id": requisition_id}
    first = client.post("/api/applications", json=payload)
    duplicate = client.post("/api/applications", json=payload)
    assert first.status_code == 201
    assert first.json()["application_id"] == f"APP-{requisition_id}-{candidates[0]}"
    assert duplicate.status_code == 409
    with factory() as session:
        attempts = session.scalars(select(models.DuplicateApplicationAttempt)).all()
    assert len(attempts) == 1
    assert attempts[0].issue_type == ExceptionType.DUPLICATE_APPLICATION


def test_screening_uses_only_job_related_rules(api):
    client, factory = api
    requisition_id, candidates = _seed_root(factory)
    application_id = _seed_application(factory, requisition_id, candidates[0])

    response = client.post(f"/api/applications/{application_id}/screen")
    assert response.status_code == 200
    result = response.json()
    assert result["score"] == "68.00"
    assert result["outcome"] == "pass"
    assert {skill["skill_id"] for skill in result["matched_skills"]} == {
        "SKILL-0001",
        "SKILL-0002",
    }
    assert [skill["skill_id"] for skill in result["missing_skills"]] == ["SKILL-0003"]
    with factory() as session:
        application = session.get(models.Application, application_id)
        assert application.status == ApplicationStatus.SCREENING
        assert application.screening_score == Decimal("68.00")


def test_interview_rejects_unavailable_and_conflicting_panelists(api):
    client, factory = api
    requisition_id, candidates = _seed_root(factory)
    application_id = _seed_application(
        factory, requisition_id, candidates[0], ApplicationStatus.INTERVIEW
    )
    now = datetime.now(timezone.utc)
    starts = now + timedelta(days=1)
    ends = starts + timedelta(hours=1)
    unavailable_id = "INTV-UNAVAILABLE"
    conflict_id = "INTV-CONFLICT"
    with factory() as session:
        for interviewer_id in (unavailable_id, conflict_id):
            session.add(
                models.Interviewer(
                    interviewer_id=interviewer_id,
                    display_name=interviewer_id,
                    email=f"{interviewer_id.lower()}@example.test",
                    is_active=True,
                )
            )
        session.add_all(
            [
                models.InterviewerAvailability(
                    availability_id="AVAIL-UNAVAILABLE",
                    interviewer_id=unavailable_id,
                    starts_at=starts - timedelta(hours=1),
                    ends_at=ends + timedelta(hours=1),
                    is_available=True,
                ),
                models.InterviewerAvailability(
                    availability_id="AVAIL-BLOCKED",
                    interviewer_id=unavailable_id,
                    starts_at=starts,
                    ends_at=ends,
                    is_available=False,
                ),
                models.InterviewerAvailability(
                    availability_id="AVAIL-CONFLICT",
                    interviewer_id=conflict_id,
                    starts_at=starts - timedelta(hours=1),
                    ends_at=ends + timedelta(hours=1),
                    is_available=True,
                ),
            ]
        )
        existing = models.Interview(
            interview_id="INT-2026-000001",
            application_id=application_id,
            round_number=1,
            interview_type=InterviewType.VIDEO,
            starts_at=starts,
            ends_at=ends,
            status=InterviewStatus.SCHEDULED,
        )
        session.add(existing)
        session.flush()
        session.execute(
            insert(models.interview_panel).values(
                interview_id=existing.interview_id,
                interviewer_id=conflict_id,
            )
        )
        session.commit()

    body = {
        "application_id": application_id,
        "round_number": 2,
        "interview_type": "technical",
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat(),
    }
    unavailable = client.post(
        "/api/interviews", json={**body, "interviewer_ids": [unavailable_id]}
    )
    conflict = client.post("/api/interviews", json={**body, "interviewer_ids": [conflict_id]})
    assert unavailable.status_code == 409
    assert unavailable.json()["error"]["code"] == "interviewer_unavailable"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "interviewer_conflict"


def test_missing_feedback_and_invalid_stage_prevent_advancement(api):
    client, factory = api
    requisition_id, candidates = _seed_root(factory, candidate_count=2)
    incomplete_application = _seed_application(
        factory, requisition_id, candidates[0], ApplicationStatus.INTERVIEW
    )
    _seed_interview(factory, incomplete_application, panel_size=2, feedback_count=1)
    missing_feedback = client.post(
        f"/api/applications/{incomplete_application}/advance",
        json={"to_status": "offer", "actor": "test:recruiter"},
    )
    assert missing_feedback.status_code == 409
    assert missing_feedback.json()["error"]["code"] == "feedback_incomplete"

    applied_application = _seed_application(factory, requisition_id, candidates[1])
    invalid_transition = client.post(
        f"/api/applications/{applied_application}/advance",
        json={"to_status": "hired", "actor": "test:recruiter"},
    )
    assert invalid_transition.status_code == 409
    assert invalid_transition.json()["error"]["code"] == "invalid_transition"


def test_offer_approval_salary_and_response_reconciliation(api):
    client, factory = api
    requisition_id, candidates = _seed_root(factory, candidate_count=2)
    application_ids = []
    for candidate_id in candidates:
        application_id = _seed_application(
            factory, requisition_id, candidate_id, ApplicationStatus.INTERVIEW
        )
        _seed_interview(factory, application_id)
        application_ids.append(application_id)

    outside_band = client.post(
        f"/api/applications/{application_ids[0]}/offers",
        json={"salary": "125000", "created_by": "test:recruiter"},
    )
    assert outside_band.status_code == 422
    assert outside_band.json()["error"]["code"] == "salary_outside_band"

    accepted_offer = client.post(
        f"/api/applications/{application_ids[0]}/offers",
        json={"salary": "100000", "created_by": "test:recruiter"},
    )
    assert accepted_offer.status_code == 201
    accepted_offer_id = accepted_offer.json()["offer_id"]
    assert client.get(f"/api/offers/{accepted_offer_id}").status_code == 200
    issue_before_approval = client.post(
        f"/api/offers/{accepted_offer_id}/issue",
        json={"actor": "test:recruiter"},
    )
    assert issue_before_approval.status_code == 409
    approved = client.post(
        f"/api/offers/{accepted_offer_id}/approve",
        json={"actor": "test:approver"},
    )
    assert approved.status_code == 200
    issued = client.post(
        f"/api/offers/{accepted_offer_id}/issue",
        json={"actor": "test:recruiter"},
    )
    assert issued.status_code == 200
    accepted = client.post(
        f"/api/offers/{accepted_offer_id}/respond",
        json={"decision": "accepted", "actor": "test:candidate"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert len(client.get("/api/offers").json()) == 1
    assert client.get(f"/api/applications/{application_ids[0]}").json()["status"] == "hired"

    rejected_offer = client.post(
        f"/api/applications/{application_ids[1]}/offers",
        json={"salary": "105000", "created_by": "test:recruiter"},
    )
    rejected_offer_id = rejected_offer.json()["offer_id"]
    assert rejected_offer.status_code == 201
    assert client.post(
        f"/api/offers/{rejected_offer_id}/approve",
        json={"actor": "test:approver"},
    ).status_code == 200
    assert client.post(
        f"/api/offers/{rejected_offer_id}/issue",
        json={"actor": "test:recruiter"},
    ).status_code == 200
    rejected = client.post(
        f"/api/offers/{rejected_offer_id}/respond",
        json={
            "decision": "rejected",
            "actor": "test:candidate",
            "reason": "Candidate declined the terms.",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.get(f"/api/applications/{application_ids[1]}").json()["status"] == "rejected"
    with factory() as session:
        accepted_events = session.scalars(
            select(models.OfferEvent).where(models.OfferEvent.offer_id == accepted_offer_id)
        ).all()
        rejected_events = session.scalars(
            select(models.OfferEvent).where(models.OfferEvent.offer_id == rejected_offer_id)
        ).all()
        rejected_final = next(
            event for event in rejected_events if event.to_status == OfferStatus.REJECTED
        )
        accepted_record = session.get(models.Offer, accepted_offer_id)
        rejected_record = session.get(models.Offer, rejected_offer_id)
    assert len(accepted_events) == len(rejected_events) == 4
    assert accepted_record.responded_at is not None
    assert rejected_record.responded_at is not None
    assert rejected_final.reason == "Candidate declined the terms."


def test_dashboard_counts_reflect_database_rows(api):
    client, factory = api
    requisition_id, candidates = _seed_root(factory)
    application_id = _seed_application(factory, requisition_id, candidates[0])
    now = datetime.now(timezone.utc)
    with factory() as session:
        application = session.get(models.Application, application_id)
        application.screening_score = Decimal("80.00")
        application.screening_explanation = {"outcome": "pass"}
        application.submitted_at = datetime(2026, 1, 15, tzinfo=timezone.utc)
        session.add(
            models.Interview(
                interview_id="INT-2026-000099",
                application_id=application_id,
                round_number=1,
                interview_type=InterviewType.PHONE,
                starts_at=now - timedelta(hours=2),
                ends_at=now - timedelta(hours=1),
                status=InterviewStatus.COMPLETED,
            )
        )
        for index, status in enumerate(
            [OfferStatus.ACCEPTED, OfferStatus.REJECTED, OfferStatus.PENDING_APPROVAL],
            start=1,
        ):
            session.add(
                models.Offer(
                    offer_id=f"OFF-2026-{index:06d}",
                    application_id=application_id,
                    salary=Decimal("100000"),
                    currency="USD",
                    status=status,
                    created_by="test:recruiter",
                )
            )
        session.commit()

    summary = client.get("/api/dashboard/summary")
    assert summary.status_code == 200
    assert summary.json() == {
        "total_requisitions": 1,
        "open_requisitions": 1,
        "candidates": 1,
        "applications": 1,
        "interviews": 1,
        "offers": 3,
        "accepted_offers": 1,
        "rejected_offers": 1,
        "pending_offers": 1,
        "pipeline": [
            {"stage": "applied", "count": 1},
            {"stage": "screening", "count": 0},
            {"stage": "interview", "count": 0},
            {"stage": "offer", "count": 0},
            {"stage": "hired", "count": 0},
            {"stage": "rejected", "count": 0},
            {"stage": "withdrawn", "count": 0},
        ],
        "scored_applications": 1,
        "average_screening_score": 80.0,
        "recorded_passes": 1,
        "recorded_fails": 0,
        "application_trend": [{"month": "2026-01", "applications": 1}],
    }
    pipeline = client.get("/api/dashboard/pipeline").json()
    assert sum(stage["count"] for stage in pipeline) == 1
    assert next(stage for stage in pipeline if stage["stage"] == "applied")["count"] == 1


def test_five_synthetic_anomalies_remain_detectable(api):
    client, factory = api
    with factory() as session:
        _insert_baseline(session, _generate_baseline("starter"))
        session.commit()
        ground_truth = _inject_anomalies(session)
        session.commit()
        report = validate_dataset(session)
    assert len(ground_truth) == 5
    assert len(report.findings) == 5
    assert {finding.issue_type for finding in report.findings} == {
        ExceptionType.UNAVAILABLE_INTERVIEWER.value,
        ExceptionType.DUPLICATE_APPLICATION.value,
        ExceptionType.MISSING_FEEDBACK_ADVANCEMENT.value,
        ExceptionType.OFFER_OUTSIDE_SALARY_BAND.value,
        ExceptionType.CLOSED_REQUISITION_ACTIVE_APPLICATIONS.value,
    }

    exceptions = client.get("/api/exceptions")
    assert exceptions.status_code == 200
    assert len(exceptions.json()) == 5
    assert all(record["detected"] for record in exceptions.json())
    assert (
        client.get(f"/api/exceptions/{exceptions.json()[0]['exception_id']}").status_code
        == 200
    )
    grouped = client.get("/api/dashboard/exceptions")
    assert grouped.status_code == 200
    assert sum(group["count"] for group in grouped.json()) == 5


def test_experience_screening_does_not_depend_on_rule_order():
    result = calculate_screening_score(
        {"SKILL-0001": (5, Decimal("4"))},
        [
            {
                "rule_type": ScreeningRuleType.MIN_EXPERIENCE,
                "criterion_name": "Relevant experience",
                "weight": Decimal("20"),
                "minimum_value": Decimal("2"),
            },
            {
                "rule_type": ScreeningRuleType.REQUIRED_SKILL,
                "skill_id": "SKILL-0001",
                "criterion_name": "Python proficiency",
                "weight": Decimal("80"),
                "minimum_proficiency": 2,
            },
        ],
    )
    assert result.score == Decimal("100.00")
    assert result.components[0]["years_experience"] == Decimal("4.00")
