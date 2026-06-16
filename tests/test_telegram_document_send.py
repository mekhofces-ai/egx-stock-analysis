from __future__ import annotations

from types import SimpleNamespace

from app.services.telegram_bot import send_private_documents_sync


def test_telegram_document_send_fails_safely_without_token(tmp_path) -> None:  # noqa: ANN001
    doc = tmp_path / "report.xlsx"
    doc.write_text("sample", encoding="utf-8")
    settings = SimpleNamespace(telegram_bot_token=None)
    result = send_private_documents_sync("Report ready", [doc], settings=settings)
    assert result["configured"] is False
    assert result["sent_documents"] == 0
