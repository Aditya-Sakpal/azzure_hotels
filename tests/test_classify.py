from scraper.classify import classify, looks_english, topics_in


def r(score, liked=None, disliked=None, title=None, lang="en"):
    return {"score": score, "liked_text": liked, "disliked_text": disliked, "title": title, "language": lang}


def test_complaint_vs_praise_split():
    c = classify(r(4, liked="Great location near the station", disliked="Room was dirty and the shower was cold"))
    assert c["sentiment"] == "negative"
    assert "cleanliness" in c["complaints"] and "bathroom" in c["complaints"]
    assert "location" in c["praise"]
    assert "location" not in c["complaints"]


def test_trivial_dislike_is_not_a_complaint():
    assert classify(r(9, disliked="Nothing"))["complaints"] == []
    assert classify(r(9, disliked="n/a"))["complaints"] == []
    assert classify(r(9, disliked="No complaints!"))["has_complaint_text"] is False


def test_sentiment_bands():
    assert classify(r(8))["sentiment"] == "positive"
    assert classify(r(7))["sentiment"] == "neutral"
    assert classify(r(5))["sentiment"] == "negative"


def test_title_only_negative_review_counts_as_complaint():
    c = classify(r(2, title="Filthy room, rude staff"))
    assert {"cleanliness", "staff"} <= set(c["complaints"])


def test_english_text_with_foreign_lang_code_is_classified():
    c = classify(r(3, disliked="The facilities are too bad, the venue is not clean, very dirty.", lang="vi"))
    assert c["classifiable"] and "cleanliness" in c["complaints"]


def test_non_english_text_is_not_classified():
    c = classify(r(3, disliked="Sengen var beskidt, rengøringen var ringe", lang="da"))
    assert not c["classifiable"] and c["complaints"] == []
    assert not looks_english("Sengen var beskidt, rengøringen var ringe")


def test_word_boundaries():
    assert "noise" not in topics_in("Hairdryer provided")          # no substring hits
    assert "cleanliness" not in topics_in("hair dryer was broken")
    assert topics_in("Very noisy at night") == ["noise"]
