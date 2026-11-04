import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ocr_app import CAPABILITIES, create_app
from ocr_worker import convert_result, pages


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RAG_OCR_API_KEY", "test-only-" * 4)
    return TestClient(create_app())


def test_requires_secret(monkeypatch):
    monkeypatch.delenv("RAG_OCR_API_KEY", raising=False)
    with pytest.raises(ValueError):
        create_app()


def test_health_is_explicit_about_capabilities(client):
    assert client.get("/health").json()["capabilities"] == CAPABILITIES
    assert CAPABILITIES["tableStructure"] is False
    assert CAPABILITIES["layoutAnalysis"] is False


def test_auth_fails_before_reading_document(client):
    assert client.post("/layout-parsing", json={"file": "sensitive"}).status_code == 401


@pytest.mark.parametrize("body", [{}, {"file": "AAAA", "fileType": True}, {"url": "http://x"}])
def test_invalid_shape_rejected(client, body):
    response = client.post(
        "/layout-parsing", json=body, headers={"Authorization": "Bearer " + "test-only-" * 4}
    )
    assert response.status_code == 422


def test_image_format_and_pixels_checked():
    with pytest.raises(ValueError, match="INVALID_PDF"):
        list(pages(b"not a pdf", 0))
    image = Image.new("RGB", (10, 10), "white")
    data = io.BytesIO()
    image.save(data, "GIF")
    with pytest.raises(ValueError, match="INVALID_IMAGE"):
        list(pages(data.getvalue(), 1))


def test_text_lines_keep_coordinates_and_page_identity():
    result = convert_result(
        {
            "rec_texts": ["合成样本"],
            "rec_polys": [[[1, 2], [30, 2], [30, 12], [1, 12]]],
            "rec_scores": [0.99],
        },
        2,
    )["prunedResult"]
    assert result["page_index"] == 2
    assert result["parsing_res_list"] == [
        {"block_label": "ocr-text", "block_content": "合成样本", "block_bbox": [1, 2, 30, 12]}
    ]


def test_no_recognizable_text_is_failure():
    with pytest.raises(ValueError, match="OCR_PAGE_HAS_NO_TEXT"):
        convert_result({"rec_texts": ["noise"], "rec_polys": [[]], "rec_scores": [0.1]}, 0)


def test_non_finite_coordinates_rejected():
    with pytest.raises(ValueError, match="OCR_INVALID_RESULT"):
        convert_result(
            {
                "rec_texts": ["bad"],
                "rec_polys": [[[0, 0], [float("nan"), 0], [1, 1], [0, 1]]],
                "rec_scores": [0.9],
            },
            0,
        )
