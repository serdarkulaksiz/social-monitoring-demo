from __future__ import annotations

import json
from pathlib import Path

import pytest

from social_incident_monitor.classification.model import training
from social_incident_monitor.classification.model.features import extract, mask_people
from social_incident_monitor.classification.model.logistic import LogisticRegression
from social_incident_monitor.classification.model.naive_bayes import NaiveBayes
from social_incident_monitor.classification.model.text_model import ModelError, TextModel
from social_incident_monitor.classification.rule_based import RuleSet

from conftest import label_rows

RULES = RuleSet.load()


def test_user_names_and_links_never_become_features() -> None:
    features = extract("@ayse_yilmaz Ziraat mobil açılmıyor https://ornek.test/x")
    assert not any("ayse" in name or "ornek" in name for name in features)
    assert "w:kullaniciadi" in features and "w:baglanti" in features
    assert mask_people("@x merhaba") == " kullaniciadi  merhaba"


def test_features_cover_misspellings_negation_and_cues() -> None:
    right, wrong = extract("açılmıyor"), extract("acilmiyo")
    shared = {name for name in right if name.startswith("c:")} & {name for name in wrong if name.startswith("c:")}
    assert {"c:<aci", "c:acil", "c:ilmi"} <= shared  # letter pieces make the misspelling look alike
    assert extract("artık sorun yok")["neg:sorun"] == 1
    assert extract("para çekemiyorum")["x:neg_verb"] == 1
    cues = extract("harika yine olmadı 👏 !!")
    assert cues["e:👏"] == 1 and cues["x:exclaim"] == 1


def test_rule_results_are_features() -> None:
    from social_incident_monitor.classification.rule_based import RuleBasedClassifier

    from conftest import START, make_post

    ruled = RuleBasedClassifier(RULES).classify(make_post("Ziraat ATM paramı vermedi"), START)
    features = extract("Ziraat ATM paramı vermedi", ruled)
    assert {"r:issue:atm_failure", "r:complaint", "r:bank"} <= set(features)


@pytest.mark.parametrize("head", [NaiveBayes, LogisticRegression])
def test_both_algorithms_learn_a_simple_split(head: type) -> None:
    examples = [(extract(f"uygulama çöktü berbat {n}"), "yes") for n in range(10)]
    examples += [(extract(f"teşekkürler harika hizmet {n}"), "no") for n in range(10)]
    model = head.train(examples)
    assert model.predict_proba(extract("uygulama yine çöktü"))["yes"] > 0.8
    assert model.predict_proba(extract("harika teşekkürler"))["no"] > 0.8
    assert head.from_dict(json.loads(json.dumps(model.to_dict()))).predict_proba(extract("çöktü")) == pytest.approx(
        model.predict_proba(extract("çöktü")), abs=1e-4
    )


@pytest.mark.parametrize("algorithm", ["nb", "logreg"])
def test_model_catches_what_the_rules_miss(algorithm: str) -> None:
    rows = label_rows()
    model = training.train(rows, RULES, algorithm)  # type: ignore[arg-type]
    prediction = model.predict("Ziraat belanızı versin, tarihi eser uygulamanızı geliştirin", None)
    assert prediction.p_complaint is not None and prediction.p_complaint > 0.8
    words = [word for word, _value in prediction.explanation]
    assert any(word in ("belanızı", "geliştirin", "tarihi", "eser") for word in words)  # shown with Turkish letters
    assert set(model.heads) == {"bank", "complaint", "service", "issue"}
    assert model.metadata["label_counts"]["complaints"] == 60


def test_training_is_repeatable() -> None:
    rows = label_rows(6)
    first = training.train(rows, RULES, "logreg", now=training.datetime(2026, 1, 1, tzinfo=training.UTC))
    second = training.train(rows, RULES, "logreg", now=training.datetime(2026, 1, 1, tzinfo=training.UTC))
    assert first.to_dict() == second.to_dict()


def test_training_needs_both_kinds_of_posts() -> None:
    only_complaints = [row for row in label_rows(4) if row["label"]["is_complaint"]]
    with pytest.raises(ValueError, match="complaints and non-complaints"):
        training.train(only_complaints, RULES, "nb")


def test_model_file_round_trip_and_tamper_check(tmp_path: Path) -> None:
    model = training.train(label_rows(6), RULES, "nb")
    path = tmp_path / "model.json"
    checksum = model.save(path)
    loaded = TextModel.load(path)
    assert loaded.version == model.version and json.loads(path.read_text(encoding="utf-8"))["sha256"] == checksum
    text = "Ziraat mobil yine açılmıyor"
    assert loaded.predict(text, None).p_complaint == pytest.approx(model.predict(text, None).p_complaint, abs=1e-4)

    data = json.loads(path.read_text(encoding="utf-8"))
    data["heads"]["complaint"]["log_priors"]["yes"] = 0.0  # someone edits a weight by hand
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ModelError, match="checksum"):
        TextModel.load(path)
    with pytest.raises(ModelError, match="not found"):
        TextModel.load(tmp_path / "missing.json")
    path.write_text('{"format": "something else"}', encoding="utf-8")
    with pytest.raises(ModelError, match="not a text model"):
        TextModel.load(path)


def test_scores_and_quality_gate() -> None:
    rows = label_rows(6)
    model = training.train(rows, RULES, "logreg")
    rules = training.evaluate(rows, RULES, "rules")
    hybrid = training.evaluate(rows, RULES, "hybrid", model)
    assert rules.rows == hybrid.rows == len(rows)
    assert (hybrid.complaint.recall or 0) > (rules.complaint.recall or 0)  # the rules miss most of these complaints
    assert set(hybrid.by_sample) == {"sorgu1", "genis", "karisan"}
    assert training.gate(hybrid, rules)[0] is True
    passed, reason = training.gate(rules, hybrid)
    assert passed is False and "recall" in reason


def test_explanations_show_words_not_rule_names() -> None:
    model = training.train(label_rows(6), RULES, "logreg")
    from social_incident_monitor.classification.rule_based import RuleBasedClassifier

    from conftest import START, make_post

    text = "Ziraat Bankası mobil yine açılmıyor"
    prediction = model.predict(text, RuleBasedClassifier(RULES).classify(make_post(text), START))
    assert prediction.explanation and not any(word.startswith(("kural", "r:", "c:")) for word, _value in prediction.explanation)


def test_hashtags_and_spam_cues_are_separate_features() -> None:
    features = extract("Ne günlerdi #çöktü #SONDAKİKA #AnaParadaEsitlik Mehmet Şimşek Sezen Taş Atilla Taş Ziraat Bankası")
    assert features["h:coktu"] == 1 and "w:coktu" not in features
    assert features["x:many_hashtags"] == 1 and features["x:name_list"] == 1
    assert extract("@a @b @c bilgi verin")["x:many_mentions"] == 1
