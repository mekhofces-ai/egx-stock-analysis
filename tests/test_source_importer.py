from app.services.source_importer import normalize_username


def test_normalize_username_accepts_at_handles() -> None:
    assert normalize_username("@egyptianborsa") == "@egyptianborsa"


def test_normalize_username_accepts_tme_links() -> None:
    assert normalize_username("https://t.me/example_channel") == "@example_channel"


def test_normalize_username_rejects_blank_values() -> None:
    assert normalize_username("") is None
