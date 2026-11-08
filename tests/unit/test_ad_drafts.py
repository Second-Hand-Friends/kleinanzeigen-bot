# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for importing online ad drafts."""

import json
import urllib.error as urllib_error
import urllib.request as urllib_request
from pathlib import Path
from typing import Any, cast

import pytest
from ruamel.yaml import YAML

from kleinanzeigen_bot import ad_drafts, ad_loading
from kleinanzeigen_bot.model.ad_model import AdPartial
from kleinanzeigen_bot.utils import dicts

pytestmark = pytest.mark.unit

ROOT_URL = "https://www.kleinanzeigen.de"
USER_ID = 12345678
DRAFT_ID = "9615b515-ab60-4f52-83b1-67069e2c1864"

# Shape of an entry returned by /users/<id>/ad-drafts.json (draft saved in the Android app).
SAMPLE_DRAFT:dict[str, Any] = {
    "creationPlatform": "ANDROID",
    "id": DRAFT_ID,
    "userId": USER_ID,
    "categoryDisplayName": "Haushaltsgeräte",
    "expiryDate": 1798804313000,
    "adImages": [
        {"canonicalUrl": "https://img.example.invalid/images/3e/one?jwt=a", "thumbnailUrl": "https://img.example.invalid/images/3e/one?jwt=b"},
        {"canonicalUrl": "https://img.example.invalid/images/92/two?jwt=c", "thumbnailUrl": "https://img.example.invalid/images/92/two?jwt=d"},
    ],
    "adType": "OFFER",
    "title": "Krups Nescafé Dolce Gusto Piccolo Kaffeemaschine",
    "categoryId": 176,
    "attributeMap": {
        "haushaltsgeraete.art_s": "kaffee_espressomaschinen",
        "haushaltsgeraete.versand_s": "",
        "haushaltsgeraete.condition_s": "",
        "haushaltsgeraete.seller_badges_s": "",
    },
    "shippingOptions": [{"shippingOptionId": "HERMES_003"}, {"shippingOptionId": "DHL_002"}],
    "priceType": "FIXED",
    "description": "Ich biete hier eine Kaffeemaschine an.\n\n- Modell: Krups KP1006",
    "buyNow": True,
}


class FakeWeb:
    """Serves canned JSON bodies for the manage-ads and drafts endpoints."""

    def __init__(self, drafts:list[dict[str, Any]], *, manage_ads_payload:dict[str, Any] | None = None) -> None:
        self.responses:dict[str, Any] = {
            f"{ROOT_URL}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=1": manage_ads_payload
            if manage_ads_payload is not None
            else {"ads": [], "bizJsonModel": {"quotaUsageAccounts": {"userAccount": {"userId": str(USER_ID)}}}},
            f"{ROOT_URL}/users/{USER_ID}/ad-drafts.json": {"listOfAdDrafts": drafts},
        }
        self.requested_urls:list[str] = []

    async def web_request(self, url:str) -> dict[str, Any]:
        self.requested_urls.append(url)
        return {"statusCode": 200, "statusMessage": "OK", "content": json.dumps(self.responses[url])}

    def timeout(self, key:str = "default") -> float:
        return 15.0


@pytest.fixture
def fake_image_download(monkeypatch:pytest.MonkeyPatch) -> list[str]:
    """Write a placeholder file instead of downloading each image."""
    downloaded:list[str] = []

    def download(url:str, directory:Path, filename_prefix:str, img_nr:int, timeout:float) -> str | None:
        downloaded.append(url)
        if "fail" in url:
            return None
        image = directory / f"{filename_prefix}{img_nr}.jpg"
        image.write_bytes(b"jpeg")
        return image.name

    monkeypatch.setattr(ad_drafts, "_download_image_sync", download)
    return downloaded


def _draft_file(drafts_dir:Path, draft_id:str = DRAFT_ID) -> Path:
    return drafts_dir / draft_id / f"ad_{draft_id}.yaml.draft"


def _load_draft(draft_file:Path) -> dict[str, Any]:
    return cast(dict[str, Any], YAML().load(draft_file.read_text(encoding = "utf-8")))


class TestDraftToAdConfig:

    def test_maps_complete_draft(self) -> None:
        ad_cfg = ad_drafts.draft_to_ad_config(SAMPLE_DRAFT, ad_drafts.load_category_paths(), ["ad__img1.jpg"])

        assert ad_cfg == {
            "active": False,
            "type": "OFFER",
            "title": "Krups Nescafé Dolce Gusto Piccolo Kaffeemaschine",
            "description": "Ich biete hier eine Kaffeemaschine an.\n\n- Modell: Krups KP1006",
            "category": "161/176/kaffee_espressomaschinen",
            "special_attributes": {"haushaltsgeraete.art_s": "kaffee_espressomaschinen"},
            "price_type": "FIXED",
            "shipping_type": "SHIPPING",
            "shipping_options": ["Hermes_M", "DHL_5"],
            "sell_directly": True,
            "images": ["ad__img1.jpg"],
        }

    def test_maps_photo_only_draft(self) -> None:
        ad_cfg = ad_drafts.draft_to_ad_config({"id": DRAFT_ID}, ad_drafts.load_category_paths(), ["ad__img1.jpg"])

        assert ad_cfg == {"active": False, "title": "", "description": "", "category": "", "sell_directly": False, "images": ["ad__img1.jpg"]}

    def test_skips_unknown_shipping_options_and_disables_direct_buy_without_options(self, caplog:pytest.LogCaptureFixture) -> None:
        draft = {**SAMPLE_DRAFT, "shippingOptions": [{"shippingOptionId": "UNKNOWN_001"}]}

        ad_cfg = ad_drafts.draft_to_ad_config(draft, ad_drafts.load_category_paths(), [])

        assert "shipping_options" not in ad_cfg
        assert "shipping_type" not in ad_cfg
        assert ad_cfg["sell_directly"] is False
        assert "skipping unknown shipping option UNKNOWN_001" in caplog.text

    def test_completed_draft_is_a_valid_ad(self) -> None:
        """Once the missing price is added, the imported draft passes the regular ad validation."""
        ad_cfg = ad_drafts.draft_to_ad_config(SAMPLE_DRAFT, ad_drafts.load_category_paths(), [])

        ad = AdPartial.model_validate({**ad_cfg, "price": 25})

        assert ad.category == "161/176/kaffee_espressomaschinen"
        assert ad.shipping_options == ["Hermes_M", "DHL_5"]


class TestResolveDraftCategory:

    @pytest.mark.parametrize(
        ("category_id", "special_attributes", "expected"),
        [
            (176, {"haushaltsgeraete.art_s": "kaffee_espressomaschinen"}, "161/176/kaffee_espressomaschinen"),
            (176, {"haushaltsgeraete.art_s": "not_a_known_sub_category"}, "161/176"),
            (176, {}, "161/176"),
            ("176", {}, "161/176"),
            (999999, {}, "999999"),
            (None, {}, ""),
            ("abc", {}, ""),
        ],
    )
    def test_resolves_leaf_id_to_category_path(self, category_id:Any, special_attributes:dict[str, str], expected:str) -> None:
        assert ad_drafts.resolve_draft_category(category_id, special_attributes, ad_drafts.load_category_paths()) == expected


class TestDownloadImage:

    def test_uses_a_finite_timeout_and_keeps_url_tokens_out_of_the_log(
        self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch, caplog:pytest.LogCaptureFixture
    ) -> None:
        url = "https://img.example.invalid/images/3e/one?jwt=secret-token"
        timeouts:list[float] = []

        def urlopen(requested_url:str, *, timeout:float) -> Any:
            timeouts.append(timeout)
            raise urllib_error.URLError(f"stalled while reading {requested_url}")

        monkeypatch.setattr(urllib_request, "urlopen", urlopen)

        assert ad_drafts._download_image_sync(url, tmp_path, "ad__img", 1, 15.0) is None
        assert timeouts == [15.0]
        assert "https://img.example.invalid/images/3e/one" in caplog.text
        assert "secret-token" not in caplog.text


class TestDownloadAdDrafts:

    @pytest.mark.asyncio
    async def test_saves_draft_file_and_images(self, tmp_path:Path, fake_image_download:list[str]) -> None:
        drafts_dir = tmp_path / "drafts"
        web = FakeWeb([SAMPLE_DRAFT])

        await ad_drafts.download_ad_drafts(cast(Any, web), drafts_dir, ROOT_URL)

        draft_file = _draft_file(drafts_dir)
        saved = _load_draft(draft_file)
        assert saved["active"] is False
        assert saved["title"] == SAMPLE_DRAFT["title"]
        assert saved["images"] == [f"ad_{DRAFT_ID}__img1.jpg", f"ad_{DRAFT_ID}__img2.jpg"]
        assert all((draft_file.parent / image).is_file() for image in saved["images"])
        assert fake_image_download == [image["canonicalUrl"] for image in SAMPLE_DRAFT["adImages"]]
        assert draft_file.read_text(encoding = "utf-8").startswith("# yaml-language-server: $schema=")
        assert not list(drafts_dir.glob(".tmp-*"))

    @pytest.mark.asyncio
    async def test_draft_file_is_not_matched_by_default_ad_file_pattern(self, tmp_path:Path, fake_image_download:list[str]) -> None:
        """Incomplete drafts must not be picked up (and fail validation) before the user renames them."""
        await ad_drafts.download_ad_drafts(cast(Any, FakeWeb([SAMPLE_DRAFT])), tmp_path / "drafts", ROOT_URL)

        assert ad_loading.discover_ad_files(str(tmp_path / "config.yaml"), ["./**/ad_*.{json,yml,yaml}"]) == {}

    @pytest.mark.asyncio
    async def test_keeps_previously_imported_draft(self, tmp_path:Path, fake_image_download:list[str]) -> None:
        drafts_dir = tmp_path / "drafts"
        await ad_drafts.download_ad_drafts(cast(Any, FakeWeb([SAMPLE_DRAFT])), drafts_dir, ROOT_URL)
        draft_file = _draft_file(drafts_dir)
        draft_file.write_text("title: edited locally\n", encoding = "utf-8")
        new_draft = {"id": "another-draft", "title": "Another draft title"}

        await ad_drafts.download_ad_drafts(cast(Any, FakeWeb([SAMPLE_DRAFT, new_draft])), drafts_dir, ROOT_URL)

        assert draft_file.read_text(encoding = "utf-8") == "title: edited locally\n"
        assert _load_draft(_draft_file(drafts_dir, "another-draft"))["title"] == "Another draft title"

    @pytest.mark.asyncio
    async def test_draft_with_failed_image_is_not_saved_and_retried_on_next_run(
        self, tmp_path:Path, fake_image_download:list[str], caplog:pytest.LogCaptureFixture
    ) -> None:
        drafts_dir = tmp_path / "drafts"
        failing_draft = {**SAMPLE_DRAFT, "adImages": [{"canonicalUrl": "https://img.example.invalid/images/fail?jwt=a"}]}

        await ad_drafts.download_ad_drafts(cast(Any, FakeWeb([failing_draft])), drafts_dir, ROOT_URL)

        assert not (drafts_dir / DRAFT_ID).exists()
        assert not list(drafts_dir.glob(".tmp-*"))
        assert "will be retried on the next run" in caplog.text

        await ad_drafts.download_ad_drafts(cast(Any, FakeWeb([SAMPLE_DRAFT])), drafts_dir, ROOT_URL)

        assert len(_load_draft(_draft_file(drafts_dir))["images"]) == 2

    @pytest.mark.asyncio
    async def test_aborts_without_account_id(self, tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
        drafts_dir = tmp_path / "drafts"
        web = FakeWeb([SAMPLE_DRAFT], manage_ads_payload = {"ads": []})

        await ad_drafts.download_ad_drafts(cast(Any, web), drafts_dir, ROOT_URL)

        assert web.requested_urls == [f"{ROOT_URL}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=1"]
        assert not drafts_dir.exists()
        assert "Could not determine the account id" in caplog.text

    @pytest.mark.asyncio
    async def test_failed_draft_does_not_abort_batch(self, tmp_path:Path, fake_image_download:list[str], monkeypatch:pytest.MonkeyPatch) -> None:
        drafts_dir = tmp_path / "drafts"
        original_save_dict = dicts.save_dict

        def save_dict(filepath:str | Path, content:dict[str, Any], *, header:str | None = None) -> None:
            if content["title"] == "broken":
                raise OSError("disk full")
            original_save_dict(filepath, content, header = header)

        monkeypatch.setattr(dicts, "save_dict", save_dict)

        await ad_drafts.download_ad_drafts(cast(Any, FakeWeb([{"id": "broken-draft", "title": "broken"}, SAMPLE_DRAFT])), drafts_dir, ROOT_URL)

        assert not (drafts_dir / "broken-draft").exists()
        assert not list(drafts_dir.glob(".tmp-*"))
        assert _draft_file(drafts_dir).is_file()
