"""테스트 공용 픽스처 — 오프라인 VWorld 응답 로더 + 가짜 requests.get."""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


class FakeResponse:
    """requests.Response 최소 흉내 (json/raise_for_status)."""

    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")


def make_fake_get(responses):
    """호출 순서대로 responses(list[dict])를 돌려주는 가짜 requests.get.

    단일 dict를 주면 매 호출 같은 응답을 반환.
    """
    if isinstance(responses, dict):
        seq = None
        single = responses
    else:
        seq = iter(responses)
        single = None

    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params or {}})
        payload = single if single is not None else next(seq)
        return FakeResponse(payload)

    fake_get.calls = calls
    return fake_get


@pytest.fixture
def fixture():
    return load_fixture
