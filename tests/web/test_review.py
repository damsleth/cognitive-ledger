"""End-to-end coverage for the web inbox review queue."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ledger.parsing.frontmatter import parse_frontmatter_text
from ledger.web.services.review import load_review_items


CANDIDATE = """---
created: 2026-08-27T10:00:00Z
updated: 2026-08-27T10:00:00Z
tags: [test, candidate]
confidence: 0.63
source: inferred
scope: dev
lang: en
type: facts
promoted_by: yaams
yaams_candidate_id: test-candidate-1
yaams_entity: SLA
{extra}---

# {title}

## Statement

{statement}
"""


def _write_candidate(
    root: Path,
    *,
    title: str = "SLA is a useful abbreviation",
    statement: str = "SLA is used in service management.",
    extra: str = "",
) -> Path:
    path = root / "notes" / "00_inbox" / "fact__sla.md"
    path.write_text(
        CANDIDATE.format(title=title, statement=statement, extra=extra),
        encoding="utf-8",
    )
    return path


def _item(client: TestClient, root: Path):
    return load_review_items(root / "notes")[0]


def _decision_data(client: TestClient, item, **overrides: str) -> dict[str, str]:
    data = {
        "csrf_token": client.app.state.review_csrf_token,  # type: ignore[attr-defined]
        "title": item.candidate.title,
        "body": item.candidate.body,
        "target_type": item.candidate.type,
        "action": "accept",
    }
    data.update(overrides)
    return data


def test_review_page_lists_candidate(client: TestClient, web_ledger_root: Path) -> None:
    _write_candidate(web_ledger_root)
    response = client.get("/review")
    assert response.status_code == 200
    assert "Review queue" in response.text
    assert "SLA is a useful abbreviation" in response.text
    assert "1 left" in response.text


def test_approve_marks_human_confirmation_and_promotes(
    client: TestClient, web_ledger_root: Path
) -> None:
    inbox_path = _write_candidate(web_ledger_root)
    item = _item(client, web_ledger_root)

    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(client, item),
    )

    assert response.status_code == 200
    assert "Review queue cleared" in response.text
    assert not inbox_path.exists()
    promoted = next((web_ledger_root / "notes" / "02_facts").glob("fact__sla*.md"))
    frontmatter, _ = parse_frontmatter_text(promoted.read_text(encoding="utf-8"))
    assert frontmatter["source"] == "user"
    assert frontmatter["reviewed_by"] == "user"
    assert float(frontmatter["confidence"]) == 0.9
    assert "reviewed_at" in frontmatter


def test_choice_fills_placeholder_and_approves(
    client: TestClient, web_ledger_root: Path
) -> None:
    _write_candidate(
        web_ledger_root,
        title="SLA means {{answer}}",
        statement="In this context, SLA means {{answer}}.",
        extra=(
            'review_question: "What does SLA mean here?"\n'
            'review_options: ["Service level agreement", "Software license agreement"]\n'
        ),
    )
    item = _item(client, web_ledger_root)
    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(
            client,
            item,
            action="",
            answer="Service level agreement",
        ),
    )

    assert response.status_code == 200
    promoted = next((web_ledger_root / "notes" / "02_facts").glob("fact__sla*.md"))
    text = promoted.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter_text(text)
    assert "SLA means Service level agreement" in body
    assert frontmatter["review_answer"] == "Service level agreement"
    assert "review_question" not in frontmatter
    assert "review_options" not in frontmatter
    assert "1 answered" in response.text


def test_unlisted_choice_is_rejected_without_mutation(
    client: TestClient, web_ledger_root: Path
) -> None:
    inbox_path = _write_candidate(
        web_ledger_root,
        title="SLA means {{answer}}",
        statement="SLA means {{answer}}.",
        extra='review_options: ["Service level agreement"]\n',
    )
    item = _item(client, web_ledger_root)
    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(client, item, action="", answer="Injected answer"),
    )

    assert response.status_code == 200
    assert "not one of this candidate&#39;s review options" in response.text
    assert inbox_path.exists()


def test_rewrite_can_reroute_to_preference(
    client: TestClient, web_ledger_root: Path
) -> None:
    _write_candidate(web_ledger_root)
    item = _item(client, web_ledger_root)
    rewritten_body = item.candidate.body.replace(
        "SLA is used in service management.",
        "Prefer expanding SLA on first use.",
    )
    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(
            client,
            item,
            title="Expand SLA on first use",
            body=rewritten_body,
            target_type="preferences",
        ),
    )

    assert response.status_code == 200
    promoted = next(
        (web_ledger_root / "notes" / "03_preferences").glob("pref__sla*.md")
    )
    assert "# Expand SLA on first use" in promoted.read_text(encoding="utf-8")


def test_ingest_digest_is_source_material_not_a_yes_no_candidate(
    client: TestClient, web_ledger_root: Path
) -> None:
    path = _write_candidate(web_ledger_root, title="Ingest summary 2026-08-27")
    digest_path = path.with_name("note__ingest_summary_2026_08_27.md")
    path.rename(digest_path)

    page = client.get("/review")
    assert page.status_code == 200
    assert "No atomic candidates" in page.text
    assert "1 source capture" in page.text
    assert "Ingest summary 2026-08-27" not in page.text
    assert "Is this correct, durable, and useful?" not in page.text
    assert load_review_items(web_ledger_root / "notes") == []
    assert digest_path.exists()


def test_explicit_rewrite_candidate_cannot_be_one_click_approved(
    client: TestClient, web_ledger_root: Path
) -> None:
    inbox_path = _write_candidate(
        web_ledger_root,
        extra="review_requires_rewrite: true\n",
    )
    item = _item(client, web_ledger_root)

    page = client.get("/review")
    assert "Rewrite required" in page.text
    assert "Extract one atomic, durable note" in page.text
    assert "Is this correct, durable, and useful?" not in page.text
    assert "Reject candidate" in page.text
    assert "review-approve" not in page.text

    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(client, item),
    )
    assert "must be rewritten into one atomic note" in response.text
    assert inbox_path.exists()

    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(
            client,
            item,
            action="rewrite_accept",
            title="SLA is used in service management",
            body="# SLA is used in service management\n\n## Statement\n\nSLA is used in service management.\n",
        ),
    )
    assert response.status_code == 200
    assert not inbox_path.exists()


def test_reject_uses_selected_reason_and_logs_signature(
    client: TestClient, web_ledger_root: Path
) -> None:
    inbox_path = _write_candidate(web_ledger_root)
    item = _item(client, web_ledger_root)
    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(client, item, action="reject", reason="duplicate"),
    )

    assert response.status_code == 200
    assert not inbox_path.exists()
    rejection_path = web_ledger_root / "notes" / "08_indices" / "rejected_candidates.jsonl"
    record = json.loads(rejection_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["reason"] == "duplicate"
    assert record["yaams_candidate_id"] == "test-candidate-1"


def test_conflict_requires_explicit_confirmation(
    client: TestClient, web_ledger_root: Path
) -> None:
    inbox_path = _write_candidate(
        web_ledger_root,
        extra=(
            "conflict_classification: contradict\n"
            'conflict_reason: "Existing note says something else"\n'
        ),
    )
    item = _item(client, web_ledger_root)
    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(client, item),
    )
    assert "require explicit confirmation" in response.text
    assert inbox_path.exists()

    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(client, item, confirm_conflict="1"),
    )
    assert response.status_code == 200
    assert not inbox_path.exists()


def test_missing_csrf_token_is_forbidden(
    client: TestClient, web_ledger_root: Path
) -> None:
    _write_candidate(web_ledger_root)
    item = _item(client, web_ledger_root)
    response = client.post(
        f"/review/{item.id}/decide",
        data={"action": "reject"},
    )
    assert response.status_code == 403


def test_skip_rotates_without_mutating(
    client: TestClient, web_ledger_root: Path
) -> None:
    first = _write_candidate(web_ledger_root, title="First candidate")
    second = web_ledger_root / "notes" / "00_inbox" / "fact__second.md"
    second.write_text(
        CANDIDATE.format(
            title="Second candidate",
            statement="Second statement.",
            extra="yaams_candidate_id: test-candidate-2\n",
        ),
        encoding="utf-8",
    )
    item = _item(client, web_ledger_root)
    response = client.post(
        f"/review/{item.id}/decide",
        data=_decision_data(client, item, action="skip"),
    )
    assert response.status_code == 200
    assert "Second candidate" in response.text
    assert first.exists() and second.exists()
