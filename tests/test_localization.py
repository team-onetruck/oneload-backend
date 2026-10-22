from app.domain.localization import Hints, VisionFeatures, VisionText, match_location


def _features(*texts: str, quality: str = "usable") -> VisionFeatures:
    return VisionFeatures(image_quality=quality, texts=[VisionText(text=t) for t in texts])


def test_more_matching_sign_text_ranks_higher(seoul):
    candidates = match_location(_features("공항철도", "Airport Railroad", "4호선"), seoul, Hints(), "en")

    assert [c.node_id for c in candidates] == ["line4-gates", "line1-gates"]
    assert candidates[0].rank_score > candidates[1].rank_score
    assert "Sign reads “4호선”" in candidates[0].evidence


def test_matching_ignores_case_spacing_and_dashes(seoul):
    candidates = match_location(_features("airport  railroad", "LINE-1"), seoul, Hints(), "ko")

    assert candidates[0].node_id == "line1-gates"
    assert candidates[0].evidence == ["“Airport Railroad” 안내판", "“Line 1” 안내판"]


def test_floor_hint_breaks_a_tie(seoul):
    tied = match_location(_features("Airport Railroad"), seoul, Hints(), "en")
    hinted = match_location(_features("Airport Railroad", "Express Train"), seoul, Hints(floor_hint="B2"), "en")

    assert tied[0].rank_score == tied[1].rank_score
    assert hinted[0].node_id == "arex-concourse"


def test_unreadable_photo_or_unknown_text_gives_no_candidates(seoul):
    assert match_location(_features("4호선", quality="blurry"), seoul, Hints(), "en") == []
    assert match_location(_features("Myeongdong"), seoul, Hints(), "en") == []
