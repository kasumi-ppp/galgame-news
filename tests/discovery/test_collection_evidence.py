from pathlib import Path
from types import SimpleNamespace
import pytest

from galgame_news.discovery.adapters import OfficialHtmlAdapter
from galgame_news.domain import CollectionContext, ImageNeed, NewsItem, SourceRef, SourceType


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "tests" / "fixtures" / "collection_263"


def _news():
    return NewsItem(
        issue_id="263", sequence=1, section="新作", title="Summer Pockets CG更新",
        body="官网更新 CG", game_names=["Summer Pockets"],
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )


def _source(url="https://key.visualarts.gr.jp/summer_ss/"):
    return SourceRef(url=url, domain="key.visualarts.gr.jp", source_type=SourceType.OFFICIAL_SITE)


def _collect_html(html, page_url="https://example.test/game/", *, scripts=None, source=None):
    requested = []

    class Client:
        def get(self, url, **kwargs):
            requested.append(url)
            body = (scripts or {}).get(url, "")
            return SimpleNamespace(text=body if url != page_url else html, url=url)

    ref = source or _source(page_url)
    result = OfficialHtmlAdapter(client=Client()).collect(_news(), ref, CollectionContext(max_candidates=2000))
    assert not result.failures
    return result, ref, requested


def _candidate(result, suffix):
    return next(item for item in result.candidates if item.image_url.endswith(suffix))


def test_frozen_key_gallery_script_expands_only_five_linked_preview_images():
    html = (AUDIT / "f019ad193b053d898746.html").read_text(encoding="utf-8")
    script_url = "https://key.visualarts.gr.jp/summer_ss/common/js/gallery.js?v=2"
    script = (AUDIT / "gallery_js_diagnostic.js").read_text(encoding="utf-8")
    result, _, requested = _collect_html(
        html, "https://key.visualarts.gr.jp/summer_ss/", scripts={script_url: script}
    )
    expanded = [item for item in result.candidates if "/common/image/spss_g_" in item.image_url]
    assert len(expanded) == 5
    assert all(item.image_url.endswith(f"spss_g_{number}.webp") for number, item in enumerate(expanded, 1))
    assert script_url in requested
    first = _candidate(result, "spss_g_1.webp")
    assert first.evidence
    assert first.evidence[0].role == "game_cg"
    assert first.evidence[0].method == "script"
    heading = _candidate(result, "gallery_heading.webp")
    assert heading.evidence[0].role == "decorative"
    assert heading.signals.get("gallery_path") is not True


def test_same_bounded_script_is_not_expanded_without_linked_gallery_targets():
    html = '<img src="/static.webp"><script src="/common/js/gallery.js"></script>'
    script = "const imageCount = 5; const images = Array.from({length: imageCount}, (_, index) => ({preview: `cg_${index + 1}.webp`}));"
    result, _, _ = _collect_html(
        html, scripts={"https://example.test/common/js/gallery.js": script}
    )
    assert [item.image_url for item in result.candidates] == ["https://example.test/static.webp"]


def test_anemoi_gallery_tabs_bind_content_to_event_and_background_roles():
    html = (AUDIT / "2b4c5ce4319557be0278.html").read_text(encoding="utf-8")
    result, _, _ = _collect_html(html, "https://key.visualarts.gr.jp/anemoi/")
    event = _candidate(result, "am_g_1.jpg")
    background = _candidate(result, "am_bg_1.jpg")
    assert event.evidence and event.evidence[0].role == "game_cg"
    assert "\u30a4\u30d9\u30f3\u30c8CG" in event.evidence[0].text
    assert "gallery_cg_box_1" in event.evidence[0].container
    assert background.evidence and background.evidence[0].role == "background_art"
    assert "\u80cc\u666fCG" in background.evidence[0].text
    assert "gallery_cg_box_2" in background.evidence[0].container


def test_product_cards_keep_image_evidence_local_to_goods_title():
    html = (AUDIT / "bfd17ff06cda09690db2.html").read_text(encoding="utf-8")
    result, _, _ = _collect_html(html, "https://www.curtain-damashii.com/item-list/")
    expected = {
        "curtain_yoakena02_heya.jpg": "\u30ab\u30fc\u30c6\u30f3\uff08\u30d5\u30a3\u30fc\u30ca\u30fb\u30d5\u30a1\u30e0\u30fb\u30a2\u30fc\u30b7\u30e5\u30e9\u30a4\u30c8/20\u5468\u5e74\uff09",
        "tape_yoakena12-big_2.jpg": "\u7279\u5927\u30bf\u30da\u30b9\u30c8\u30ea\u30fc\uff08",
        "rubbermat_yoakena06_1.jpg": "\u30e9\u30d0\u30fc\u30de\u30c3\u30c8\uff08",
        "rubbermat_daitoshokan04_1.jpg": "\u30e9\u30d0\u30fc\u30de\u30c3\u30c8\uff08",
    }
    for suffix, title in expected.items():
        candidate = _candidate(result, suffix)
        assert candidate.evidence
        assert candidate.evidence[0].role == "goods"
        assert title in candidate.evidence[0].text


def test_site_verification_meta_is_not_an_age_gate_and_css_background_is_decorative():
    result, source, _ = _collect_html(
        '<meta name="google-site-verification" content="token"><div style="background-image:url(/skin.webp)"></div>',
        source=_source("https://example.test/game/"),
    )
    assert not result.manual_review_reasons
    image = _candidate(result, "skin.webp")
    assert image.evidence
    assert image.evidence[0].role == "decorative"
    assert source.requires_review is False


def test_unresolved_dynamic_gallery_requests_rendered_review():
    html = '<section id="gallery"><div id="galleryPreview"></div></section><script>mountGallery()</script>'
    result, _, _ = _collect_html(html)
    assert OfficialHtmlAdapter.needs_render(html, result)


def test_related_products_and_filename_hints_do_not_override_local_role():
    result,_,_ = _collect_html('''<section id="gallery"><img src="arrow_scene.png" alt="事件 CG"></section>
        <aside class="related products"><div class="product-card"><h3 class="product-title">Other Game</h3><span class="price">1000円</span><img src="goods.jpg"></div></aside>''')
    assert _candidate(result,"arrow_scene.png").evidence[0].role == "game_cg"
    assert _candidate(result,"goods.jpg").evidence[0].role == "decorative"


def test_card_main_image_and_its_css_variant_are_goods_but_page_skin_is_decoration():
    html = '''<a class="product-card" href="/products/detail/item-1/">
    <div class="product-card-image-container" style="background:url(main-small.jpg)"><img src="main.jpg"></div>
    <div class="product-card-product-name">Summer Pockets acrylic figure</div><span class="product-price-display-value">￥1000</span></a>
    <section id="gallery" style="background:url(skin.jpg)"></section>'''
    result,_,_ = _collect_html(html)
    for suffix in ("main.jpg","main-small.jpg"):
        c=_candidate(result,suffix)
        assert c.evidence[0].role == "goods"
        assert c.evidence[0].item_id == "products/detail/item-1"
    assert _candidate(result,"skin.jpg").evidence[0].role == "decorative"


def test_image_banner_role_wins_over_parent_gallery():
    result,_,_ = _collect_html('<section id="gallery"><img class="banner" src="promo.png" alt="Banner"></section>')
    assert _candidate(result,"promo.png").evidence[0].role == "banner"
    assert _candidate(result,"promo.png").signals.get("gallery_path") is not True


@pytest.mark.parametrize("meta", ["google-site-verification","baidu-site-verification","google-site-verification-token","baidu-site-verification-other","site-verification"])
def test_site_verification_metadata_never_implies_age_gate(meta):
    result,source,_ = _collect_html(f'<meta name="{meta}"><img src="photo.jpg">')
    assert not source.requires_review
    assert result.candidates and not result.manual_review_reasons


def test_safe_inline_gallery_literal_inherits_thumbnail_relation():
    html = '''<section id="gallery"><img id="galleryPreview" src="fallback.png"><div id="galleryThumbnailTrack"></div></section>
    <script>const images=[{preview:'cg.png',thumbnail:'small.png'}];
    document.getElementById('galleryPreview');document.getElementById('galleryThumbnailTrack');</script>'''
    result,_,_= _collect_html(html)
    cg=_candidate(result,"cg.png")
    assert cg.evidence[0].method == "script"
    assert cg.evidence[0].variant_of.endswith("small.png")
    assert not OfficialHtmlAdapter.needs_render(html,result)


def test_script_gallery_in_background_tab_keeps_background_role():
    html = '''<button aria-controls="gallery">背景CG</button><section id="gallery"><img id="galleryPreview" src="fallback.png"><div id="galleryThumbnailTrack"></div></section>
    <script>const images=[{preview:'scene.png'}]; document.getElementById('galleryPreview');document.getElementById('galleryThumbnailTrack');</script>'''
    result,_,_= _collect_html(html)
    assert _candidate(result,"scene.png").evidence[0].role == "background_art"
