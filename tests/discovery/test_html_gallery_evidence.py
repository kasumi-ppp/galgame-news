"""Offline regressions for DOM-local gallery image relationships."""

from types import SimpleNamespace

import pytest

from galgame_news.discovery.adapters import OfficialHtmlAdapter
from galgame_news.domain import CollectionContext, ImageNeed, NewsItem, SourceRef, SourceType


def collect(html, *, page="https://official.example/game/gallery/"):
    class Client:
        def get(self, url):
            return SimpleNamespace(text=html, url=url)

    news = NewsItem(
        issue_id="262", sequence=1, section="新作", title="One Night After CG更新",
        body="官网更新 CG", game_names=["One Night After"],
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )
    source = SourceRef(url=page, domain="official.example", source_type=SourceType.OFFICIAL_SITE)
    result = OfficialHtmlAdapter(client=Client()).collect(news, source, CollectionContext(max_candidates=100))
    assert not result.failures
    return {candidate.image_url: candidate for candidate in result.candidates}


def candidate(candidates, filename):
    return candidates["https://official.example/" + filename]


def test_linked_full_image_inherits_local_gallery_evidence_and_provenance():
    images = collect('''<section id="GALLERY"><a href="/full.jpg">
      <img src="/thumb.jpg" alt="Scene CG"></a></section>''')
    full = candidate(images, "full.jpg")
    assert full.signals["gallery_path"] is True
    assert full.signals["gallery_evidence_source"] == "dom:section#GALLERY via anchor[href]"
    assert full.signals["media_variant_of"] == "https://official.example/thumb.jpg"
    assert full.image_alt == "Scene CG"
    assert "https://official.example/thumb.jpg" not in images


def test_picture_formats_and_largest_srcset_share_only_their_fallback_image_context():
    images = collect('''<div id="GRAPHIC"><picture>
      <source type="image/webp" srcset="/scene-small.webp 600w, /scene-full.webp 1800w">
      <source type="image/avif" data-srcset="/scene-full.avif 1800w">
      <img src="/scene-thumb.jpg" alt="Scene CG" srcset="/scene-small.jpg 600w, /scene-full.jpg 1800w">
      </picture></div>
      <picture><source srcset="/unrelated.webp 1800w"><img src="/unrelated.jpg"></picture>''')
    for filename in ("scene-full.webp", "scene-full.avif", "scene-full.jpg"):
        image = candidate(images, filename)
        assert image.signals["gallery_path"] is True
        assert image.signals["media_variant_of"] == "https://official.example/scene-thumb.jpg"
        assert "srcset" in image.signals["gallery_evidence_source"]
        assert image.image_alt == "Scene CG"
    assert candidate(images, "unrelated.webp").signals.get("gallery_path") is not True
    assert candidate(images, "unrelated.webp").image_alt is None


@pytest.mark.parametrize("attribute", ["data-original", "data-full", "data-large", "data-hires", "data-zoom-image"])
@pytest.mark.parametrize("wrapper", [False, True])
def test_full_size_image_attributes_share_the_same_dom_item(attribute, wrapper):
    attr = f'{attribute}="/original.jpg"'
    html = (f'<section id="gallery"><figure {attr}><img src="/preview.jpg" alt="Scene CG"></figure></section>'
            if wrapper else f'<section id="gallery"><img src="/preview.jpg" {attr} alt="Scene CG"></section>')
    images = collect(html)
    original = candidate(images, "original.jpg")
    assert original.signals["gallery_path"] is True
    assert attribute in original.signals["gallery_evidence_source"]
    assert original.signals["media_variant_of"] == "https://official.example/preview.jpg"


def test_meta_page_title_and_gallery_filename_do_not_create_local_gallery_evidence():
    images = collect('''<html><head><title>One Night After GALLERY</title>
      <meta property="og:image" content="/gallery/og.jpg"></head><body>
      <header><img src="/header.jpg"></header>
      <div id="GALLERY"><img src="/cg.jpg"></div>
      <div><img src="/gallery/unrelated.jpg" alt="Scene CG"></div>
      <section id="special"><img src="/special.jpg"></section>
      </body></html>''')
    assert candidate(images, "cg.jpg").signals["gallery_path"] is True
    for filename in ("gallery/og.jpg", "header.jpg", "gallery/unrelated.jpg", "special.jpg"):
        image = candidate(images, filename)
        assert image.signals.get("gallery_path") is not True
        assert "gallery_evidence_source" not in image.signals


@pytest.mark.parametrize("role", ["logo", "character", "goods"])
def test_own_non_cg_role_overrides_gallery_container_for_all_picture_variants(role):
    images = collect(f'''<div id="GALLERY"><picture>
      <source srcset="/full.webp 1800w"><img src="/preview.jpg" alt="{role}" data-full="/full.jpg">
      </picture></div>''')
    for filename in ("preview.jpg", "full.jpg", "full.webp"):
        image = candidate(images, filename)
        assert image.signals.get("gallery_path") is not True
        assert image.signals["gallery_excluded_reason"] == role


def test_explicit_modal_reference_propagates_gallery_evidence_to_target_picture():
    images = collect('''<section id="gallery"><a href="#scene-modal"><img src="/thumb.jpg" alt="Scene CG"></a></section>
      <div id="scene-modal"><picture><source srcset="/full.webp 1800w"><img src="/full.jpg"></picture></div>
      <div id="other-modal"><img src="/other.jpg"></div>''')
    for filename in ("full.jpg", "full.webp"):
        image = candidate(images, filename)
        assert image.signals["gallery_path"] is True
        assert "anchor[href] -> #scene-modal/" in image.signals["gallery_evidence_source"]
        assert image.signals["media_variant_of"] == "https://official.example/thumb.jpg"
        assert image.image_alt == "Scene CG"
    assert candidate(images, "other.jpg").signals.get("gallery_path") is not True


def test_shared_wrapper_or_modal_with_multiple_images_does_not_link_unrelated_items():
    images = collect('''<div id="gallery"><a href="#shared-modal"><img src="/thumb.jpg"></a></div>
      <div id="shared-modal" data-full="/shared-full.jpg"><img src="/first.jpg"><img src="/second.jpg"></div>''')
    for filename in ("first.jpg", "second.jpg"):
        assert candidate(images, filename).signals.get("gallery_path") is not True
        assert "media_variant_of" not in candidate(images, filename).signals


def test_page_level_gallery_css_and_navigation_do_not_mark_unrelated_images():
    images = collect('''<html><body class="gallery"><div class="site-header">
      <img class="gallery" src="/navigation.jpg"></div><img src="/unrelated.jpg">
      <section id="GALLERY"><img src="/scene.jpg"></section></body></html>''')
    assert candidate(images, "navigation.jpg").signals.get("gallery_path") is not True
    assert candidate(images, "unrelated.jpg").signals.get("gallery_path") is not True
    assert candidate(images, "scene.jpg").signals["gallery_path"] is True


def test_generic_nested_section_keeps_its_gallery_ancestor_evidence():
    images = collect('''<div id="GALLERY"><section class="scene"><img src="/scene.jpg"></section>
      <section class="goods"><img src="/goods.jpg"></section></div>''')
    assert candidate(images, "scene.jpg").signals["gallery_path"] is True
    assert candidate(images, "goods.jpg").signals.get("gallery_path") is not True


def test_fragment_gallery_under_header_still_has_an_excluded_role():
    images = collect('<header><div id="GALLERY"><img src="/header.jpg"></div></header>',
                     page="https://official.example/game/#GALLERY")
    assert candidate(images, "header.jpg").signals.get("gallery_path") is not True
    assert candidate(images, "header.jpg").signals["gallery_excluded_reason"] == "header"


@pytest.mark.parametrize("trigger,attribute,selector", [
    ("a", "data-target", "#scene-modal"),
    ("button", "data-bs-target", "#scene-modal"),
    ("div", "data-izimodal-open", "#scene-modal"),
    ("img", "aria-controls", "scene-modal"),
])
def test_explicit_static_modal_attributes_link_only_the_selected_dom_target(trigger, attribute, selector):
    attr = f'{attribute}="{selector}"'
    control = (f'<img src="/thumb.jpg" {attr}>' if trigger == "img" else
               f'<{trigger} {attr}><img src="/thumb.jpg"></{trigger}>')
    images = collect(f'<div id="GALLERY">{control}</div><div id="scene-modal"><img src="/full.jpg"></div>')
    full = candidate(images, "full.jpg")
    assert full.signals["gallery_path"] is True
    assert full.signals["media_variant_of"] == "https://official.example/thumb.jpg"
    assert attribute in full.signals["gallery_evidence_source"]
