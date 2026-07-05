import os
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.billing import select_web_checkout_price_id


@contextmanager
def stripe_env(**values):
    keys = {
        "STRIPE_PRICE_ID",
        "STRIPE_PRICE_ID_BETA_PRO",
        "STRIPE_PRICE_ID_PRO",
        "STRIPE_PRICE_ID_PREMIUM",
        "STRIPE_PRICE_ID_QUALITY",
        "STRIPE_PRICE_ID_ELITE",
    }
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key in keys:
            os.environ.pop(key, None)
        for key, value in previous.items():
            if value is not None:
                os.environ[key] = value


def test_beta_pro_uses_single_public_99_price_id_with_legacy_premium_fallback():
    with stripe_env(STRIPE_PRICE_ID="price_generic"):
        assert select_web_checkout_price_id("beta_pro") == "price_generic"
    with stripe_env(STRIPE_PRICE_ID="price_generic", STRIPE_PRICE_ID_PREMIUM="price_premium"):
        assert select_web_checkout_price_id("premium") == "price_premium"
        assert select_web_checkout_price_id("beta_pro") == "price_premium"
    with stripe_env(STRIPE_PRICE_ID="price_generic", STRIPE_PRICE_ID_PREMIUM="price_premium", STRIPE_PRICE_ID_BETA_PRO="price_beta"):
        assert select_web_checkout_price_id("beta_pro") == "price_beta"


def test_non_curated_tiers_use_generic_price_when_specific_price_is_blank():
    with stripe_env(STRIPE_PRICE_ID="price_generic", STRIPE_PRICE_ID_PRO="", STRIPE_PRICE_ID_PREMIUM=""):
        assert select_web_checkout_price_id("pro") == "price_generic"
        assert select_web_checkout_price_id("premium") == "price_generic"


def test_non_curated_tiers_prefer_specific_price_over_generic_price():
    with stripe_env(
        STRIPE_PRICE_ID="price_generic",
        STRIPE_PRICE_ID_PRO="price_pro",
        STRIPE_PRICE_ID_PREMIUM="price_premium",
    ):
        assert select_web_checkout_price_id("pro") == "price_pro"
        assert select_web_checkout_price_id("premium") == "price_premium"


def test_curated_tiers_require_specific_price_and_do_not_use_generic_price():
    with stripe_env(STRIPE_PRICE_ID="price_generic", STRIPE_PRICE_ID_QUALITY="", STRIPE_PRICE_ID_ELITE=""):
        assert select_web_checkout_price_id("quality") == ""
        assert select_web_checkout_price_id("elite") == ""


def test_curated_tiers_use_their_specific_price():
    with stripe_env(STRIPE_PRICE_ID_QUALITY="price_quality", STRIPE_PRICE_ID_ELITE="price_elite"):
        assert select_web_checkout_price_id("quality") == "price_quality"
        assert select_web_checkout_price_id("elite") == "price_elite"


if __name__ == "__main__":
    test_non_curated_tiers_use_generic_price_when_specific_price_is_blank()
    test_non_curated_tiers_prefer_specific_price_over_generic_price()
    test_curated_tiers_require_specific_price_and_do_not_use_generic_price()
    test_curated_tiers_use_their_specific_price()
    print("billing price selection checks passed")
