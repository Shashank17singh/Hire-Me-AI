import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend import main


def extract(html: str) -> str:
    parser = main.TextExtractor()
    parser.feed(html)
    return parser.get_text()


def test_text_is_extracted_from_html() -> None:
    assert extract("<h1>Shashank</h1><p>Engineer</p>") == "Shashank\nEngineer"


def test_scripts_and_styles_are_dropped() -> None:
    text = extract("<style>p {color: red}</style><script>alert(1)</script><p>Kept</p>")

    assert text == "Kept"


def test_external_links_are_kept_as_text() -> None:
    assert "(Link: https://example.com)" in extract(
        '<a href="https://example.com">site</a>'
    )


def test_relative_links_are_ignored() -> None:
    assert "Link" not in extract('<a href="/about">about</a>')


def test_chat_explains_when_no_resume_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_cached_resume", None)
    monkeypatch.setattr(main, "refresh_cache", lambda: None)

    reply = main.chat(main.ChatRequest(question="Tell me about yourself"))

    assert "couldn't access" in reply["answer"]


def test_chat_answers_from_the_cached_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    resume = main.Resume(name="Shashank")
    monkeypatch.setattr(main, "_cached_resume", resume)
    monkeypatch.setattr(
        main, "ask_candidate", lambda question, cached, _: f"{cached.name}: {question}"
    )

    reply = main.chat(main.ChatRequest(question="Hi"))

    assert reply == {"answer": "Shashank: Hi"}


@pytest.mark.parametrize("question", ["", "x" * 1001])
def test_empty_and_oversized_questions_are_rejected(question: str) -> None:
    with pytest.raises(ValidationError):
        main.ChatRequest(question=question)


def test_refresh_is_limited_to_protect_the_paid_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(main, "_last_refresh", float("-inf"))
    monkeypatch.setattr(main, "_cached_resume", main.Resume(name="Shashank"))
    monkeypatch.setattr(main, "refresh_cache", lambda: calls.append(1))

    main.refresh()
    with pytest.raises(HTTPException) as error:
        main.refresh()

    assert error.value.status_code == 429
    assert len(calls) == 1


def test_one_client_cannot_use_the_whole_allowance() -> None:
    limiter = main.RateLimiter(per_client=2, overall=10, window_seconds=60)

    assert [limiter.allow("a", now=0) for _ in range(3)] == [True, True, False]
    assert limiter.allow("b", now=0)


def test_the_overall_limit_holds_when_clients_change_their_address() -> None:
    limiter = main.RateLimiter(per_client=1, overall=3, window_seconds=60)

    results = [limiter.allow(f"forged-{n}", now=0) for n in range(5)]

    assert results == [True, True, True, False, False]


def test_limits_recover_after_the_window() -> None:
    limiter = main.RateLimiter(per_client=1, overall=1, window_seconds=60)

    assert limiter.allow("a", now=0)
    assert not limiter.allow("a", now=59)
    assert limiter.allow("a", now=60)


def test_chat_returns_429_over_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        main, "chat_limiter", main.RateLimiter(1, 10, window_seconds=60)
    )
    monkeypatch.setattr(main, "_cached_resume", main.Resume(name="Shashank"))
    monkeypatch.setattr(main, "ask_candidate", lambda *_: "hello")
    client = TestClient(main.app)

    first = client.post("/chat", json={"question": "hi"})
    second = client.post("/chat", json={"question": "hi"})

    assert first.status_code == 200
    assert second.status_code == 429
