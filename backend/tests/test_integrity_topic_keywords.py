"""CHECK_A tokenises the topic title into words: 'Regulus (Turner)' yields 'turner', not '(turner)'.

cycle_1_20261114_0000 failed QC with "found ['regulus'] out of ['regulus', '(turner)']" although its
narration named Turner: a parenthesised token can never appear in spoken narration.
"""
from backend.app.content.content_integrity_gate import ContentIntegrityGate


def _check_a(tmp_path, title, narration):
    gate = ContentIntegrityGate(workspace_root=str(tmp_path))
    report = gate.validate_production(
        topic_title=title, category="Art", narration_text=narration, visual_beat_configs=[],
        video_path=str(tmp_path / "missing.mp4"), thumbnail_path=str(tmp_path / "missing.png"),
        thumbnail_headline="REGULUS", metadata_title=title, metadata_description=narration,
        metadata_tags=["art"], attribution_text="")
    return next(c for c in report.checks if c.check_name == "CHECK_A_TOPIC_NARRATION")


def test_parenthesised_title_word_matches_narration(tmp_path):
    c = _check_a(tmp_path, "Regulus (Turner)",
                 "Regulus is an oil painting by the English artist J. M. W. Turner, housed in Tate Britain.")
    assert c.passed, c.details


def test_narration_that_never_names_the_topic_still_fails(tmp_path):
    c = _check_a(tmp_path, "Regulus (Turner)", "A harbour scene painted in 1828 and overpainted twice, now in London.")
    assert not c.passed


def test_hyphen_and_comma_titles_still_tokenise(tmp_path):
    c = _check_a(tmp_path, "Space-junk economics, explained",
                 "Space junk is an economics problem: who pays to clear orbit?")
    assert c.passed, c.details
