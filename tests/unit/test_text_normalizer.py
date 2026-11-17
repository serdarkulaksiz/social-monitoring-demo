from social_incident_monitor.classification.text_normalizer import fold


def test_turkish_dotted_and_dotless_i() -> None:
    assert fold("ZİRAAT IĞDIR") == "ziraat igdir"


def test_diacritics_removed_and_apostrophes_joined() -> None:
    assert fold("ATM'den paramı çekemedim!") == "atmden parami cekemedim"


def test_question_mark_kept_as_token_and_urls_removed() -> None:
    assert fold("Faiz oranları nedir? https://example.invalid/x") == "faiz oranlari nedir ?"
