"""Tests for adapters/twitter_news.py — no live CLI calls."""
from unittest.mock import patch, MagicMock

from adapters.twitter_news import (
    TwitterNewsAdapter, TwitterResult, _sentiment_from_tweets,
    format_twitter_prompt_section,
)


def test_sentiment_bullish():
    tweets = [{"text": "going long SOL pump breakout"}, {"text": "buy the dip moon"}]
    assert _sentiment_from_tweets(tweets) == "bullish"


def test_sentiment_bearish():
    tweets = [{"text": "short this dump rekt"}, {"text": "sell fade bearish"}]
    assert _sentiment_from_tweets(tweets) == "bearish"


def test_sentiment_mixed():
    tweets = [{"text": "long pump moon"}, {"text": "short dump crash"}]
    assert _sentiment_from_tweets(tweets) == "mixed"


def test_sentiment_unknown():
    assert _sentiment_from_tweets([]) == "unknown"
    assert _sentiment_from_tweets([{"text": "hello world"}]) == "unknown"


def test_fetch_symbol_cli_failure():
    adapter = TwitterNewsAdapter()
    with patch("subprocess.run", side_effect=Exception("CLI not found")):
        result = adapter.fetch_symbol("SOL")
    assert result.available is False
    assert "CLI not found" in result.error


def test_fetch_symbol_success():
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = (
        "ok: true\n"
        "data:\n"
        "- text: SOL going long pump\n"
        "  author:\n"
        "    screenName: trader1\n"
    )
    adapter = TwitterNewsAdapter()
    with patch("subprocess.run", return_value=mock):
        result = adapter.fetch_symbol("SOL")
    assert result.available is True
    assert result.symbol == "SOL"
    assert len(result.tweets) == 1
    assert result.top_accounts == ["trader1"]


def test_fetch_symbol_caches():
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "ok: true\ndata:\n- text: test\n"
    adapter = TwitterNewsAdapter()
    with patch("subprocess.run", return_value=mock) as mock_run:
        adapter.fetch_symbol("SOL")
        adapter.fetch_symbol("SOL")  # Should hit cache
    assert mock_run.call_count == 1


def test_fetch_returns_datapoints():
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "ok: true\ndata:\n- text: SOL pump\n"
    adapter = TwitterNewsAdapter()
    with patch("subprocess.run", return_value=mock):
        points = adapter.fetch({"symbols": ["SOL", "BTC"]})
    assert len(points) == 2
    assert points[0].metric == "twitter_ct_intel"
    assert points[0].symbol == "SOL"
    assert isinstance(points[0].value, dict)
    assert "sentiment" in points[0].value


def test_fetch_caps_symbols():
    adapter = TwitterNewsAdapter()
    with patch.object(adapter, "fetch_symbol", return_value=TwitterResult(symbol="X")):
        points = adapter.fetch({"symbols": ["A", "B", "C", "D", "E", "F", "G"]})
    assert len(points) == 5  # MAX_SYMBOLS_PER_RUN


def test_format_prompt_section_unavailable():
    results = [TwitterResult(symbol="SOL", available=False, error="timeout")]
    section = format_twitter_prompt_section(results)
    assert "Unavailable" in section


def test_format_prompt_section_available():
    results = [TwitterResult(
        symbol="SOL", available=True, sentiment_summary="bullish",
        tweets=[{"text": "SOL breakout incoming long"}],
        top_accounts=["trader1"],
    )]
    section = format_twitter_prompt_section(results)
    assert "SOL" in section
    assert "bullish" in section
    assert "## Twitter CT Intel" in section
    assert "trader1" in section


def test_format_prompt_section_empty_list():
    section = format_twitter_prompt_section([])
    assert "Unavailable" in section


def test_health_check_ok():
    mock = MagicMock()
    mock.returncode = 0
    adapter = TwitterNewsAdapter()
    with patch("subprocess.run", return_value=mock):
        health = adapter.health_check()
    assert health.healthy is True
    assert health.name == "twitter-cli"


def test_health_check_fail():
    adapter = TwitterNewsAdapter()
    with patch("subprocess.run", side_effect=FileNotFoundError("no binary")):
        health = adapter.health_check()
    assert health.healthy is False
