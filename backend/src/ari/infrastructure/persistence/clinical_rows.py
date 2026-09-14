from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ari.infrastructure.persistence.base import Base

CLINICAL_JSON = JSON()


class SourceRow(Base):
    __tablename__ = "clinical_sources"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(CLINICAL_JSON)


class RubricRow(Base):
    __tablename__ = "clinical_rubrics"
    __table_args__ = (UniqueConstraint("id", "version", "content_hash"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(CLINICAL_JSON)


class TerminologyRow(Base):
    __tablename__ = "clinical_terminology"
    __table_args__ = (UniqueConstraint("id", "version", "content_hash"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(CLINICAL_JSON)


class ClinicalCaseRow(Base):
    __tablename__ = "clinical_cases"
    __table_args__ = (UniqueConstraint("id", "version", "content_hash"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(CLINICAL_JSON)


class CaseSourceRow(Base):
    __tablename__ = "clinical_case_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "case_version"], ["clinical_cases.id", "clinical_cases.version"]
        ),
    )
    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_version: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("clinical_sources.id"), primary_key=True)


class ScenarioRow(Base):
    __tablename__ = "clinical_scenarios"
    __table_args__ = (
        Index(
            "uq_clinical_published_case_phase",
            "case_id",
            "case_version",
            "phase",
            unique=True,
            sqlite_where=text("status = 'published'"),
        ),
        UniqueConstraint("id", "version", "content_hash"),
        ForeignKeyConstraint(
            ["case_id", "case_version", "case_hash"],
            ["clinical_cases.id", "clinical_cases.version", "clinical_cases.content_hash"],
        ),
        ForeignKeyConstraint(
            ["rubric_id", "rubric_version", "rubric_hash"],
            ["clinical_rubrics.id", "clinical_rubrics.version", "clinical_rubrics.content_hash"],
        ),
        ForeignKeyConstraint(
            ["terminology_id", "terminology_version", "terminology_hash"],
            [
                "clinical_terminology.id",
                "clinical_terminology.version",
                "clinical_terminology.content_hash",
            ],
        ),
        CheckConstraint(
            "status IN ('draft_unvalidated', 'published', 'withdrawn')",
            name="ck_clinical_workflow_status",
        ),
        CheckConstraint(
            "phase IN ('arzt_patient', 'arzt_arzt', 'fachbegriffe', 'arztbrief')",
            name="ck_clinical_phase",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(CLINICAL_JSON)
    case_id: Mapped[str] = mapped_column(String)
    case_version: Mapped[str] = mapped_column(String)
    case_hash: Mapped[str] = mapped_column(String(64))
    rubric_id: Mapped[str] = mapped_column(String)
    rubric_version: Mapped[str] = mapped_column(String)
    rubric_hash: Mapped[str] = mapped_column(String(64))
    terminology_id: Mapped[str] = mapped_column(String)
    terminology_version: Mapped[str] = mapped_column(String)
    terminology_hash: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)


class ReviewRow(Base):
    __tablename__ = "clinical_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_id", "scenario_version", "scenario_hash"],
            [
                "clinical_scenarios.id",
                "clinical_scenarios.version",
                "clinical_scenarios.content_hash",
            ],
        ),
    )
    sequence: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String, unique=True)
    scenario_id: Mapped[str] = mapped_column(String)
    scenario_version: Mapped[str] = mapped_column(String)
    scenario_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(CLINICAL_JSON)


class PublicationEventRow(Base):
    __tablename__ = "clinical_publication_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_id", "scenario_version"],
            ["clinical_scenarios.id", "clinical_scenarios.version"],
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String)
    scenario_version: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(CLINICAL_JSON)


class ClinicalSessionPinRow(Base):
    __tablename__ = "clinical_session_pins"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_id", "scenario_version", "scenario_hash"],
            [
                "clinical_scenarios.id",
                "clinical_scenarios.version",
                "clinical_scenarios.content_hash",
            ],
        ),
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String)
    scenario_version: Mapped[str] = mapped_column(String)
    scenario_hash: Mapped[str] = mapped_column(String(64))


def scenario_snapshot(row: ScenarioRow) -> dict[str, str]:
    return {
        "scenario_id": row.id,
        "scenario_version": row.version,
        "scenario_hash": row.content_hash,
        "case_id": row.case_id,
        "case_version": row.case_version,
        "case_hash": row.case_hash,
        "rubric_id": row.rubric_id,
        "rubric_version": row.rubric_version,
        "rubric_hash": row.rubric_hash,
        "terminology_id": row.terminology_id,
        "terminology_version": row.terminology_version,
        "terminology_hash": row.terminology_hash,
        "scoring_version": "assessment-weighted-v1",
    }
