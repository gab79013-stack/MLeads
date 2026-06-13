from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "web" / "app.py"


def _app_source() -> str:
    return APP.read_text(encoding="utf-8")


def test_swipe_quota_counts_all_viewed_leads_not_only_likes():
    src = _app_source()

    assert "Count every viewed/swiped lead for quota and counter consistency" in src
    assert "SELECT COUNT(*) FROM swipe_actions WHERE anon_id = ? AND action = 'like'" not in src
    assert "SELECT COUNT(*) FROM swipe_actions WHERE user_id = ? AND action = 'like'" not in src
