import pytest
from pydantic import ValidationError

from scopepilot.analysis_schemas import (
    AnalysisResult,
    ProposedTask,
    Requirement,
    ScopeAssessment,
    validate_analysis_against_brief,
)


def sample_valid_data() -> dict:
    return {
        "requirements": [
            {
                "id": "R1",
                "type": "feature",
                "description": "Add dark mode toggle to the user dashboard",
                "source_quote": "Please add a dark mode toggle to the dashboard.",
                "needs_clarification": False,
            }
        ],
        "clarification_questions": [
            "Should dark mode persist across browser sessions?"
        ],
        "proposed_tasks": [
            {
                "title": "Implement dark mode toggle",
                "description": "Add a switcher in user settings and persist theme state in local storage.",
                "type": "feature",
                "requirement_ids": ["R1"],
                "acceptance_criteria": [
                    "Toggle switches between light and dark themes",
                    "Selected theme persists on reload",
                ],
                "blocked_by_clarification": False,
            }
        ],
        "scope_assessment": {
            "status": "within_scope",
            "reason": "Baseline scope includes full user dashboard customization.",
            "related_requirement_ids": ["R1"],
        },
        "deadline_quote": "by end of this week",
    }


def test_1_valid_analysis_is_accepted():
    data = sample_valid_data()
    msg = "Please add a dark mode toggle to the dashboard. Need it by end of this week."
    scope = "Full dashboard customization with modern UI themes."

    result = AnalysisResult.model_validate(data, context={"message": msg, "original_scope": scope})
    validate_analysis_against_brief(result, msg, scope)
    assert len(result.requirements) == 1
    assert result.requirements[0].id == "R1"
    assert result.deadline_quote == "by end of this week"


def test_2_missing_required_fields_are_rejected():
    data = sample_valid_data()
    del data["scope_assessment"]

    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data)
    assert "scope_assessment" in str(exc.value)


def test_3_extra_fields_are_rejected():
    data = sample_valid_data()
    data["extra_unsupported_key"] = "surprise"

    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data)
    assert "extra_unsupported_key" in str(exc.value)


def test_4_invalid_requirement_types_are_rejected():
    data = sample_valid_data()
    data["requirements"][0]["type"] = "magical_wish"

    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data)
    assert "type" in str(exc.value)


def test_5_duplicate_and_nonsequential_requirement_ids_are_rejected():
    data = sample_valid_data()
    data["requirements"].append(
        {
            "id": "R1",
            "type": "bug",
            "description": "Fix audio playback on mobile",
            "source_quote": "audio",
            "needs_clarification": False,
        }
    )

    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data)
    assert "Requirement IDs must be sequential starting at R1" in str(exc.value)


def test_6_orphan_task_requirement_ids_are_rejected():
    data = sample_valid_data()
    data["proposed_tasks"][0]["requirement_ids"] = ["R99"]

    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data)
    assert "references non-existent requirement ID 'R99'" in str(exc.value)


def test_7_unsupported_source_quotes_are_rejected():
    data = sample_valid_data()
    data["requirements"][0]["source_quote"] = "Paraphrased text never written by client."
    msg = "Please add a dark mode toggle to the dashboard."
    scope = "Dashboard theme settings."

    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data, context={"message": msg, "original_scope": scope})
    assert "source quote is not an exact substring" in str(exc.value)

    # Also test via explicit validation function
    res = AnalysisResult.model_validate(data)
    with pytest.raises(ValueError) as exc_func:
        validate_analysis_against_brief(res, msg, scope)
    assert "not an exact substring" in str(exc_func.value)


def test_8_invented_deadline_quotes_are_rejected():
    data = sample_valid_data()
    data["deadline_quote"] = "December 25, 2026"
    msg = "Please add a dark mode toggle to the dashboard."
    scope = "Dashboard theme settings."

    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data, context={"message": msg, "original_scope": scope})
    assert "deadline_quote is not an exact substring" in str(exc.value)

    res = AnalysisResult.model_validate(data)
    with pytest.raises(ValueError) as exc_func:
        validate_analysis_against_brief(res, msg, scope)
    assert "not an exact substring" in str(exc_func.value)


def test_9_missing_original_scope_forces_unknown_status():
    data = sample_valid_data()
    data["scope_assessment"]["status"] = "within_scope"
    data["deadline_quote"] = None
    msg = "Please add a dark mode toggle to the dashboard."

    # None scope
    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data, context={"message": msg, "original_scope": None})
    assert "Scope assessment status must be 'unknown'" in str(exc.value)

    res = AnalysisResult.model_validate(data)
    with pytest.raises(ValueError) as exc_func:
        validate_analysis_against_brief(res, msg, original_scope=None)
    assert "Scope assessment status must be 'unknown'" in str(exc_func.value)

    # Whitespace-only scope
    with pytest.raises(ValueError) as exc_ws:
        validate_analysis_against_brief(res, msg, original_scope="   ")
    assert "Scope assessment status must be 'unknown'" in str(exc_ws.value)


def test_10_unreasonable_field_lengths_are_rejected():
    data = sample_valid_data()
    data["requirements"][0]["description"] = "A" * 501

    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data)
    assert "description" in str(exc.value)

    data = sample_valid_data()
    data["proposed_tasks"][0]["title"] = "T" * 151
    with pytest.raises(ValidationError) as exc2:
        AnalysisResult.model_validate(data)
    assert "title" in str(exc2.value)


def test_11_empty_requirements_cannot_produce_tasks():
    data = sample_valid_data()
    data["requirements"] = []
    data["scope_assessment"]["related_requirement_ids"] = []

    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data)
    assert "Proposed tasks cannot be created when requirements is empty" in str(exc.value)


def test_clarification_questions_rejects_nested_objects():
    data = sample_valid_data()
    data["clarification_questions"] = [{"clarification_question": "Where should the progress bar appear?"}]
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_suspicious_empty_extraction_is_rejected_on_actionable_brief():
    # Library application failure reproduction
    msg = "Please add a search box to the library page and fix the broken login button."
    data = {
        "requirements": [],
        "clarification_questions": ["What search features are desired?"],
        "proposed_tasks": [],
        "scope_assessment": {
            "status": "unknown",
            "reason": "Initial review",
            "related_requirement_ids": [],
        },
        "deadline_quote": None,
    }
    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data, context={"message": msg, "original_scope": None})
    assert "Suspiciously empty analysis" in str(exc.value)

    res = AnalysisResult.model_validate(data)
    with pytest.raises(ValueError) as exc2:
        validate_analysis_against_brief(res, msg, None)
    assert "Suspiciously empty analysis" in str(exc2.value)


def test_suspicious_empty_extraction_claiming_no_brief_is_rejected():
    msg = "Here is some context about our system."
    data = {
        "requirements": [],
        "clarification_questions": ["Please provide the client brief."],
        "proposed_tasks": [],
        "scope_assessment": {
            "status": "unknown",
            "reason": "No client brief was supplied to extract from.",
            "related_requirement_ids": [],
        },
        "deadline_quote": None,
    }
    with pytest.raises(ValidationError) as exc:
        AnalysisResult.model_validate(data, context={"message": msg, "original_scope": None})
    assert "Model claimed no brief was supplied" in str(exc.value)


def test_legitimate_empty_extraction_is_accepted_on_non_actionable_message():
    msg = "Hello, hope you are having a nice day. Thanks for connecting."
    data = {
        "requirements": [],
        "clarification_questions": ["How can we assist you with your project?"],
        "proposed_tasks": [],
        "scope_assessment": {
            "status": "unknown",
            "reason": "No actionable requirements identified in greeting message.",
            "related_requirement_ids": [],
        },
        "deadline_quote": None,
    }
    res = AnalysisResult.model_validate(data, context={"message": msg, "original_scope": None})
    validate_analysis_against_brief(res, msg, None)
    assert len(res.requirements) == 0
    assert len(res.proposed_tasks) == 0
