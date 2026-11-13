"""Shared screening calculations and recruitment lifecycle rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any

from .models import (
    ApplicationStatus,
    OfferStatus,
    RequisitionStatus,
    ScreeningRuleType,
)


@dataclass(frozen=True)
class ScreeningScore:
    score: Decimal
    required_skills_met: bool
    pass_threshold: Decimal
    components: list[dict[str, Any]]

    @property
    def passed(self) -> bool:
        return self.required_skills_met and self.score >= self.pass_threshold


def _decimal(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _field(rule: Mapping[str, Any] | Any, name: str) -> Any:
    return rule[name] if isinstance(rule, Mapping) else getattr(rule, name)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def calculate_screening_score(
    candidate_skills: Mapping[str, tuple[int, Decimal]],
    rules: Sequence[Mapping[str, Any] | Any],
) -> ScreeningScore:
    components: list[dict[str, Any]] = []
    required_met = True
    relevant_years = max(
        (
            candidate_skills.get(_field(rule, "skill_id"), (0, Decimal("0")))[1]
            for rule in rules
            if _enum_value(_field(rule, "rule_type"))
            in {
                ScreeningRuleType.REQUIRED_SKILL.value,
                ScreeningRuleType.WEIGHTED_SKILL.value,
            }
            and _field(rule, "skill_id") is not None
        ),
        default=Decimal("0"),
    )
    weighted_total = Decimal("0")
    total_weight = Decimal("0")
    pass_threshold = Decimal("55.00")

    for rule in rules:
        rule_type = _enum_value(_field(rule, "rule_type"))
        if rule_type == ScreeningRuleType.PASS_THRESHOLD.value:
            minimum_value = _field(rule, "minimum_value")
            if minimum_value is not None:
                pass_threshold = _decimal(minimum_value)
            continue

        weight = _field(rule, "weight")
        if rule_type in {
            ScreeningRuleType.REQUIRED_SKILL.value,
            ScreeningRuleType.WEIGHTED_SKILL.value,
        }:
            skill_id = _field(rule, "skill_id")
            skill = candidate_skills.get(skill_id) if skill_id else None
            proficiency = skill[0] if skill else 0
            minimum = _field(rule, "minimum_proficiency") or 1
            is_required = rule_type == ScreeningRuleType.REQUIRED_SKILL.value
            if is_required:
                criterion_score = Decimal(100 if proficiency >= minimum else 0)
                if proficiency < minimum:
                    required_met = False
            else:
                criterion_score = (
                    Decimal(proficiency * 20) if proficiency >= minimum else Decimal("0")
                )
            components.append(
                {
                    "criterion": _field(rule, "criterion_name"),
                    "score": _decimal(criterion_score),
                    "weight": weight,
                    "proficiency": proficiency,
                    "required": is_required,
                }
            )
        elif rule_type == ScreeningRuleType.MIN_EXPERIENCE.value:
            minimum_years = _field(rule, "minimum_value") or Decimal("0")
            criterion_score = min(
                Decimal("100"),
                (relevant_years / minimum_years * Decimal("100"))
                if minimum_years
                else Decimal("100"),
            )
            components.append(
                {
                    "criterion": _field(rule, "criterion_name"),
                    "score": _decimal(criterion_score),
                    "weight": weight,
                    "years_experience": _decimal(relevant_years),
                }
            )
        else:
            continue
        weighted_total += _decimal(criterion_score) * weight
        total_weight += weight

    score = _decimal(weighted_total / total_weight) if total_weight else Decimal("0.00")
    return ScreeningScore(score, required_met, pass_threshold, components)


APPLICATION_TRANSITIONS = {
    None: {ApplicationStatus.APPLIED.value},
    ApplicationStatus.APPLIED.value: {
        ApplicationStatus.SCREENING.value,
        ApplicationStatus.REJECTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.SCREENING.value: {
        ApplicationStatus.INTERVIEW.value,
        ApplicationStatus.REJECTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.INTERVIEW.value: {
        ApplicationStatus.OFFER.value,
        ApplicationStatus.REJECTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.OFFER.value: {
        ApplicationStatus.HIRED.value,
        ApplicationStatus.REJECTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.HIRED.value: set(),
    ApplicationStatus.REJECTED.value: set(),
    ApplicationStatus.WITHDRAWN.value: set(),
}

LIFECYCLE_TRANSITIONS = {
    "requisition": {
        None: {RequisitionStatus.DRAFT.value},
        RequisitionStatus.DRAFT.value: {RequisitionStatus.PENDING_APPROVAL.value},
        RequisitionStatus.PENDING_APPROVAL.value: {RequisitionStatus.OPEN.value},
        RequisitionStatus.OPEN.value: {
            RequisitionStatus.ON_HOLD.value,
            RequisitionStatus.CLOSED.value,
        },
        RequisitionStatus.ON_HOLD.value: {
            RequisitionStatus.OPEN.value,
            RequisitionStatus.CLOSED.value,
        },
        RequisitionStatus.CLOSED.value: set(),
    },
    "offer": {
        None: {OfferStatus.PENDING_APPROVAL.value},
        OfferStatus.PENDING_APPROVAL.value: {
            OfferStatus.APPROVED.value,
            OfferStatus.WITHDRAWN.value,
        },
        OfferStatus.APPROVED.value: {
            OfferStatus.ISSUED.value,
            OfferStatus.WITHDRAWN.value,
        },
        OfferStatus.ISSUED.value: {
            OfferStatus.ACCEPTED.value,
            OfferStatus.DECLINED.value,
            OfferStatus.REJECTED.value,
            OfferStatus.WITHDRAWN.value,
        },
        OfferStatus.ACCEPTED.value: set(),
        OfferStatus.DECLINED.value: set(),
        OfferStatus.REJECTED.value: set(),
        OfferStatus.WITHDRAWN.value: set(),
    },
}


def _value(status: str | Enum | None) -> str | None:
    return status.value if isinstance(status, Enum) else status


def is_application_transition_valid(
    previous: str | Enum | None, current: str | Enum
) -> bool:
    return _value(current) in APPLICATION_TRANSITIONS.get(_value(previous), set())


def is_lifecycle_transition_valid(
    entity: str, previous: str | Enum | None, current: str | Enum
) -> bool:
    transitions = LIFECYCLE_TRANSITIONS.get(entity)
    if transitions is None:
        return False
    return _value(current) in transitions.get(_value(previous), set())
