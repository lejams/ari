"""Segment offsets use the extractor's boundaries and Python Unicode code points."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from ari.backoffice_api.dto import ReviewSeenRequest
from ari.backoffice_api.routes_protocols import _source_length, _source_parts, _valid_passage
from ari.content.ports import ContentTransaction


def test_shared_page_boundaries_skip_earlier_marker_and_accept_unicode_offsets() -> None:
    text = "Other case NEXT case\n😀 before\nCASE current\nUnmapped passage.\nNEXT case\nAfter"
    segments = (
        SimpleNamespace(id="next", index=8, page_from=1, page_to=1, start_marker="NEXT case"),
        SimpleNamespace(id="own", index=2, page_from=1, page_to=1, start_marker="CASE current"),
    )
    head = SimpleNamespace(segment_id="own", document_id="doc")
    _, pages = _source_parts(
        cast(ContentTransaction, None), cast(Any, head), segments=segments, page_texts={1: text}
    )
    assert pages == [
        {
            "page_number": 1,
            "text": text,
            "start": text.index("CASE current"),
            "end": text.index("NEXT case", text.index("CASE current")),
        }
    ]
    start = text.index("Unmapped")
    passage = f"1:{start}:{start + len('Unmapped passage.')}"
    assert _valid_passage(passage, pages)
    assert not _valid_passage(f"1:0:{start}", pages)
    assert not _valid_passage(
        f"1:{text.index('NEXT case', text.index('CASE current'))}:{len(text)}", pages
    )
    assert not _valid_passage("2:0:1", pages)
    assert _source_length(pages) == len(text[pages[0]["start"] : pages[0]["end"]])


def test_review_seen_dto_accepts_real_offset_ids_and_rejects_malformed_ids() -> None:
    body = {"version": 2, "protocol_hash": "a" * 64, "passage_id": "2:12:35"}
    assert ReviewSeenRequest.model_validate(body).passage_id == "2:12:35"
    with pytest.raises(ValidationError):
        ReviewSeenRequest.model_validate({**body, "passage_id": "2:-1:35"})
