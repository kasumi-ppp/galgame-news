import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch


class ImagePrescanTests(unittest.TestCase):
    """The implementation mutations these tests catch are named per test."""

    def make_docx(self, path, paragraphs):
        body = "".join(
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
        )
        document = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>"
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document)

    def test_docx_parser_preserves_paragraphs_and_splits_concatenated_urls(self):
        """Fails if OOXML paragraphs or adjacent HTTPS links are discarded."""
        from image_prescan import parse_docx_paragraphs, split_urls

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "weekly.docx"
            self.make_docx(source, ["甲", "https://a.example/x/https://x.com/studio/status/1"])
            self.assertEqual(parse_docx_paragraphs(source), ["甲", "https://a.example/x/https://x.com/studio/status/1"])
        self.assertEqual(split_urls("https://a.example/x/https://x.com/studio/status/1"), ["https://a.example/x/", "https://x.com/studio/status/1"])

    def test_article_segmentation_keeps_sections_and_accepts_missing_final_hash(self):
        """Fails if a contributor heading is not recognized or section context leaks."""
        from image_prescan import segment_articles

        articles = segment_articles(["新作", "星海#小王#", "正文", "https://example.com/a", "旧作", "回忆#小李", "正文二"])
        self.assertEqual([(item["section"], item["title"], item["author"]) for item in articles], [("新作", "星海", "小王"), ("旧作", "回忆", "小李")])

    def test_intent_detection_classifies_static_images_and_chinese_count(self):
        """Fails if image-news language is missed or a count is guessed incorrectly."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("官方公开了三张事件CG。"), ("event_cg", 3))
        self.assertEqual(detect_image_intent("背景图片新增2枚"), ("background", 2))
        self.assertEqual(detect_image_intent("发售日公布。"), ("none", 0))

    def test_intent_requires_an_announcement_near_the_image_noun(self):
        """Fails if an incidental CG-gallery mention starts an unrelated image hunt."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("游戏包含CG鉴赏模式，发售日公布。"), ("none", 0))

    def test_happy_weekend_greeting_image_is_an_illustration(self):
        """Fails if the established Happy Weekend 贺图 announcement is missed."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("Happy Weekend 贺图"), ("illustration", 1))

    def test_happy_weekend_special_case_respects_unpublished_and_future_language(self):
        """Fails if the named-title shortcut fetches a greeting image that is not available yet."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("Happy Weekend尚未公开贺图。"), ("none", 0))
        self.assertEqual(detect_image_intent("Happy Weekend宣布明天公开贺图。"), ("none", 0))
        self.assertEqual(detect_image_intent("Happy Weekend 贺图未公开。"), ("none", 0))
        self.assertEqual(detect_image_intent("Happy Weekend 贺图不会公开。"), ("none", 0))
        self.assertEqual(detect_image_intent("Happy Weekend 贺图将于9月公开。"), ("none", 0))
        self.assertEqual(detect_image_intent("Happy Weekend 贺图预定9月公开。"), ("none", 0))
        self.assertEqual(detect_image_intent("Happy Weekend 贺图将在9月公开。"), ("none", 0))
        self.assertEqual(detect_image_intent("Happy Weekend 贺图稍后公开。"), ("none", 0))

    def test_intent_count_uses_the_image_noun_not_an_unrelated_year(self):
        """Fails if a year is mistaken for the number of newly announced CGs."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("2026年官方公开了2张CG。"), ("event_cg", 2))

    def test_count_does_not_borrow_a_later_illustration_or_cross_a_clause(self):
        """Fails if an uncounted CG borrows a count attached to later artwork."""
        from image_prescan import detect_image_intent

        for sentence in (
            "官方公开了CG，另附两张贺图。",
            "官方公开了CG；另附两张贺图。",
            "官方公开了CG和两张贺图。",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_image_intent(sentence), ("event_cg", 1))

    def test_unpublished_and_future_announcements_have_no_image_intent(self):
        """Fails if negated or scheduled publication is treated as available artwork."""
        from image_prescan import detect_image_intent

        for sentence in (
            "官方尚未公开两张CG。",
            "官方宣布明天公开两张CG。",
            "官方暂未更新两张CG。",
            "官方没有发布一张贺图。",
            "官方计划公开两张CG。",
            "官方即将公开两张CG。",
            "官方将在9月公开两张CG。",
            "官方下月公开两张CG。",
            "官方稍后公开两张CG。",
            "官方尚未公开新情报，其中包括两张CG。",
            "官方宣布明天发布更新内容，其中包含一张贺图。",
            "两张CG尚未公开。",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_image_intent(sentence), ("none", 0))

    def test_current_publication_is_not_suppressed_by_another_future_action(self):
        """Fails if a negation or schedule incorrectly suppresses a separate real update."""
        from image_prescan import detect_image_intent

        cases = {
            "官方已公开两张CG。": ("event_cg", 2),
            "官方今天更新了两张CG。": ("event_cg", 2),
            "官方明天公开发售日，今天更新了两张CG。": ("event_cg", 2),
            "官方尚未公布发售日但公开了两张CG。": ("event_cg", 2),
            "官方尚未公开CG，但今天公开了两张CG。": ("event_cg", 2),
        }
        for sentence, expected in cases.items():
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_image_intent(sentence), expected)

    def test_unpublished_images_do_not_attempt_source_or_candidate_fetching(self):
        """Fails if unavailable artwork enters either fetch stage in an online run."""
        from image_prescan import run_prescan

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "weekly.docx"
            output = Path(tmp) / "out"
            self.make_docx(source, [
                "新作", "未公开#编辑#", "官方尚未公开两张CG。", "https://official.example/pending",
                "预告#编辑#", "官方宣布明天公开两张CG。", "https://official.example/future",
            ])
            fetched = []

            def external_fetch(url, **kwargs):
                fetched.append(url)
                return b'<img src="/cg/image.png">', "text/html"

            with patch("image_prescan._fetch", side_effect=external_fetch):
                run_prescan(source, output=output)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            for article in manifest["articles"]:
                self.assertEqual(article["image_intent"], "none")
                self.assertEqual(article["expected_image_count"], 0)
                self.assertEqual(article["status"], "no_new_image")
                self.assertEqual(article["source_attempts"], [])
                self.assertEqual(article["candidates"], [])
            self.assertEqual(fetched, [])

    def test_intent_count_parses_compound_cjk_numbers_next_to_illustrations(self):
        """Fails if 十一张插图 is reduced to a single illustration."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("官方公开十一张插图。"), ("illustration", 11))

    def test_key_visual_includes_the_common_main_visual_wording(self):
        """Fails if 主视觉/主視覺 announcements are omitted from the approved clue types."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("官方公开了主视觉。"), ("key_visual", 1))
        self.assertEqual(detect_image_intent("官方公開了主視覺圖。"), ("key_visual", 1))

    def test_real_259_image_sentences_are_detected_with_clause_scoped_actions(self):
        """Fails if real publication verbs or long Happy Weekend clauses are missed."""
        from image_prescan import detect_image_intent

        cases = {
            "Happy Weekend，并附上了一张由原画綾瀬はづき绘制的贺图。": ("illustration", 1),
            "官方公开了位于京都的4张背景图像。": ("background", 4),
            "官网上更新了一张CG。": ("event_cg", 1),
            "官网上更新了两张CG。": ("event_cg", 2),
            "天獄官网上更新了两张CG。": ("event_cg", 2),
        }
        for sentence, expected in cases.items():
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_image_intent(sentence), expected)

    def test_feature_description_clauses_do_not_trigger_image_intent(self):
        """Fails if CG鉴赏/CG模式 is mistaken for an image publication."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("公开发售日，游戏包含CG鉴赏模式。"), ("none", 0))
        self.assertEqual(detect_image_intent("游戏包含CG鉴赏模式，本周发布体验版。"), ("none", 0))

    def test_sentence_level_actions_detect_comma_delimited_counted_images(self):
        """Fails if a comma wrongly separates an update action from its counted image noun."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("官方公开了更新内容，包括两张CG。"), ("event_cg", 2))
        self.assertEqual(detect_image_intent("官方发布了新情报，其中有一张贺图。"), ("illustration", 1))

    def test_explicit_update_elaboration_accepts_qizhong_inclusion_connectors(self):
        """Fails if 其中包括/其中包含 cannot elaborate a counted image-news update."""
        from image_prescan import detect_image_intent

        cases = {
            "官方公开了新情报，其中包括两张CG。": ("event_cg", 2),
            "官方发布了更新内容，其中包括一张贺图。": ("illustration", 1),
            "官方公布了更新内容，其中包含两张CG。": ("event_cg", 2),
        }
        for sentence, expected in cases.items():
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_image_intent(sentence), expected)

    def test_explicit_update_elaboration_preserves_object_and_connector_boundaries(self):
        """Fails if inclusion connectors license releases, features, or distant updates."""
        from image_prescan import detect_image_intent

        for sentence in (
            "官方公布发售日，其中包括两张CG。",
            "官方发布了体验版，其中包含4张背景图片。",
            "官方公开了系统介绍，其中包括20张CG。",
            "官方公布了新情报，其中收录两张CG。",
            "官方发布了更新内容，其中内含一张贺图。",
            "官方公开了新情报，游戏包含20张CG。",
            "官方公布了更新内容；其中包含两张CG。",
            "官方公布了更新内容，其中包含CG鉴赏模式。",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_image_intent(sentence), ("none", 0))

    def test_generic_update_does_not_revive_a_removed_cg_feature_description(self):
        """Fails if an ordinary system-introduction announcement is mistaken for image news."""
        from image_prescan import detect_image_intent

        self.assertEqual(detect_image_intent("官网公开了系统介绍，游戏有CG鉴赏功能。"), ("none", 0))

    def test_counted_game_features_are_not_objects_of_unrelated_announcements(self):
        """Fails if a publication verb licenses an unrelated counted game feature."""
        from image_prescan import detect_image_intent

        for sentence in (
            "官方公布发售日，游戏收录20张CG。",
            "游戏包含20张CG供玩家鉴赏；官方公布发售日。",
            "官方发布体验版，游戏内含4张背景图片。",
            "官方公开了系统介绍，其中包含20张CG。",
            "官方公布发售日并介绍游戏收录20张CG。",
            "官方公开了更新内容，游戏包含20张CG。",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_image_intent(sentence), ("none", 0))

    def test_separate_image_action_overrides_only_its_own_feature_mention(self):
        """Fails if an incidental first noun hides or supplies the count for a later update."""
        from image_prescan import detect_image_intent

        cases = {
            "游戏收录20张CG，官网另外更新了两张CG。": ("event_cg", 2),
            "官方公布发售日，并附上了一张贺图。": ("illustration", 1),
            "官方发布体验版并公开了4张背景图片。": ("background", 4),
            "官方公开了系统介绍，另外更新了两张CG。": ("event_cg", 2),
        }
        for sentence, expected in cases.items():
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_image_intent(sentence), expected)

    def test_unrelated_counted_features_produce_no_source_attempt_or_fallback(self):
        """Fails if counted product features enter the online image-fetch workflow."""
        from image_prescan import run_prescan

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "weekly.docx"
            output = Path(tmp) / "out"
            self.make_docx(source, ["新作", "作品#作者#", "官方公布发售日，游戏收录20张CG。", "https://official.example/news"])
            # Keep the real inspector and reporting code; only prevent external I/O.
            with patch("image_prescan._fetch", return_value=(b"<html></html>", "text/html")):
                run_prescan(source, output=output)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            article = manifest["articles"][0]
            self.assertEqual(article["status"], "no_new_image")
            self.assertEqual(article["source_attempts"], [])
            self.assertEqual(article["candidates"], [])

    @unittest.skipUnless(Path(r"E:\114514\259.docx").is_file(), "external 259.docx fixture is unavailable")
    def test_external_259_docx_has_nine_new_articles_and_five_image_announcements(self):
        """Fails if the supplied real weekly DOCX regresses its expected editorial counts."""
        from image_prescan import run_prescan

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "prescan"
            run_prescan(Path(r"E:\114514\259.docx"), output=output, offline=True)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        image_articles = [article for article in manifest["articles"] if article["image_intent"] != "none"]
        self.assertEqual(manifest["totals"]["articles"], 9)
        self.assertEqual(len(image_articles), 5)
        self.assertEqual([article["expected_image_count"] for article in image_articles], [1, 4, 1, 2, 2])

    def test_candidate_extraction_scores_article_art_above_site_chrome(self):
        """Fails if parser ignores lazy/srcset links or ranks navigation assets first."""
        from image_prescan import extract_candidates

        html = '''<meta property="og:image" content="/images/hero.jpg">
        <img alt="新规イベントCG" data-src="/cg/event_01.png" srcset="/cg/event_01.png 1x, /cg/event_01@2x.png 2x">
        <img src="/assets/logo.png" alt="site logo"><a href="/gallery/keyvisual.webp">画像</a>'''
        candidates = extract_candidates(html, "https://official.example/news/page")
        urls = [candidate["url"] for candidate in candidates]
        self.assertIn("https://official.example/cg/event_01.png", urls)
        self.assertIn("https://official.example/gallery/keyvisual.webp", urls)
        self.assertLess(urls.index("https://official.example/cg/event_01.png"), urls.index("https://official.example/assets/logo.png"))

    def test_candidate_extraction_skips_malformed_urls_without_losing_valid_ones(self):
        """Fails if one malformed img URL aborts inspection of the whole source page."""
        from image_prescan import extract_candidates

        html = '<img src="http://["><img src="/cg/valid.jpg">'
        candidates = extract_candidates(html, "https://official.example/news")
        self.assertEqual([item["url"] for item in candidates], ["https://official.example/cg/valid.jpg"])

    def test_manual_review_is_required_for_x_age_gate_and_uncertain_dynamic_pages(self):
        """Fails if restricted or indeterminate sources are reported as exact matches."""
        from image_prescan import inspect_source

        self.assertIn("x_source_unavailable", inspect_source("https://x.com/studio/status/1", fetcher=lambda *_: "")["manual_review"])
        self.assertIn("age_gated", inspect_source("https://official.example/18", fetcher=lambda *_: "成人向け 年齢確認")["manual_review"])
        self.assertIn("dynamic_page_no_static_image", inspect_source("https://site.wixsite.com/news", fetcher=lambda *_: "<div id='app'></div>")["manual_review"])

    def test_malformed_source_url_becomes_a_nonfatal_manual_review(self):
        """Fails if one corrupt DOCX source URL prevents all output files from being written."""
        from image_prescan import run_prescan

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "weekly.docx"
            output = Path(tmp) / "out"
            self.make_docx(source, ["新作", "坏链接#编辑#", "官方公开一张CG。", "https://["])
            run_prescan(source, output=output)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            attempt = manifest["articles"][0]["source_attempts"][0]
            self.assertEqual(attempt["status"], "manual_review")
            self.assertIn("invalid_source_url", attempt["manual_review"])
            self.assertTrue((output / "review.csv").is_file())

    def test_download_deduplicates_by_hash_and_identifies_png_dimensions(self):
        """Fails if duplicate bytes produce two files or image dimensions are omitted."""
        from image_prescan import download_candidates

        png = bytes.fromhex("89504e470d0a1a0a0000000d494844520000000200000003080200000000000000")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            results = download_candidates(
                [{"url": "https://a.example/one.png"}, {"url": "https://b.example/two.png"}],
                target,
                downloader=lambda *_: (png, "image/png"),
            )
            self.assertEqual(len({item["sha256"] for item in results}), 1)
            self.assertEqual(len(list(target.iterdir())), 1)
            self.assertEqual(results[0]["dimensions"], {"width": 2, "height": 3})
            self.assertEqual(results[0]["sha256"], hashlib.sha256(png).hexdigest())

    def test_download_rejects_a_lying_image_content_type_without_image_magic(self):
        """Fails if an HTML/error payload with image/png MIME is written as an image."""
        from image_prescan import download_candidates

        with tempfile.TemporaryDirectory() as tmp:
            results = download_candidates(
                [{"url": "https://official.example/image.png"}],
                Path(tmp),
                downloader=lambda *_: (b"<html>not an image</html>", "image/png"),
            )
            self.assertFalse(results[0]["downloaded"])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_download_rejects_truncated_jpeg_signature(self):
        """Fails if a two-byte JPEG signature is archived as a usable candidate image."""
        from image_prescan import download_candidates

        with tempfile.TemporaryDirectory() as tmp:
            results = download_candidates(
                [{"url": "https://official.example/broken.jpg"}],
                Path(tmp),
                downloader=lambda *_: (b"\xff\xd8truncated", "image/jpeg"),
            )
            self.assertFalse(results[0]["downloaded"])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_download_deduplicates_resized_reencoded_images_across_articles(self):
        """Fails if a thumbnail and its re-encoded original are stored twice in one weekly run."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is optional; perceptual dedupe is unavailable")
        from image_prescan import download_candidates

        original = Image.new("RGB", (80, 40))
        for x in range(80):
            for y in range(40):
                original.putpixel((x, y), ((x * 3) % 256, (y * 6) % 256, ((x + y) * 2) % 256))
        first_bytes = io.BytesIO()
        original.save(first_bytes, format="JPEG", quality=94)
        thumb_bytes = io.BytesIO()
        original.resize((40, 20)).save(thumb_bytes, format="JPEG", quality=82)
        payloads = [first_bytes.getvalue(), thumb_bytes.getvalue()]
        exact, perceptual = {}, []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = download_candidates([{"url": "https://a.example/cg.jpg"}], root / "one", downloader=lambda *_: (payloads[0], "image/jpeg"), known_hashes=exact, known_perceptual=perceptual)
            second = download_candidates([{"url": "https://b.example/cg_thumb.jpg"}], root / "two", downloader=lambda *_: (payloads[1], "image/jpeg"), known_hashes=exact, known_perceptual=perceptual)
            self.assertTrue(first[0]["downloaded"])
            self.assertTrue(second[0]["duplicate"])
            self.assertEqual(second[0]["duplicate_kind"], "perceptual")
            self.assertEqual(len(list(root.rglob("*.jpg"))), 1)

    def test_cli_rejects_nonfinite_and_nonpositive_timeout(self):
        """Fails if NaN, infinity, or zero reaches networking code as a timeout."""
        from image_prescan import main

        for value in ("nan", "inf", "0"):
            with self.subTest(value=value):
                with self.assertRaises(SystemExit) as caught:
                    with redirect_stderr(io.StringIO()):
                        main(["missing.docx", "--timeout", value])
                self.assertEqual(caught.exception.code, 2)

    def test_url_fragments_remain_source_body_not_contributor_headings(self):
        """Fails if a URL #fragment is split into a fake title and author."""
        from image_prescan import segment_articles

        articles = segment_articles(["新作", "作品#作者#", "https://official.example/news#gallery"])
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "作品")
        self.assertEqual(articles[0]["source_urls"], ["https://official.example/news#gallery"])

    def test_real_259_paragraph_section_fixture_selects_exactly_nine_new_titles(self):
        """Fails if 汉化 is absorbed into the prior article and its entries stay in 新作."""
        from image_prescan import segment_articles

        paragraphs = ["新作"]
        for number in range(1, 10):
            paragraphs.extend([f"新作{number}#编辑#", "发售日公布"])
        paragraphs.append("汉化")
        for number in range(1, 4):
            paragraphs.extend([f"汉化{number}#编辑#", "汉化版公布"])
        paragraphs.extend(["补充资料"] * (259 - len(paragraphs)))
        self.assertEqual(len(paragraphs), 259)
        articles = segment_articles(paragraphs)
        self.assertEqual(len([article for article in articles if article["section"] == "新作"]), 9)

    def test_run_offline_writes_manifest_csv_and_chinese_report_without_fallback_images(self):
        """Fails if end-to-end output omits status rows or assigns candidates to no-image news."""
        from image_prescan import run_prescan

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "weekly.docx"
            output = root / "out"
            self.make_docx(source, ["新作", "星海#小王#", "公开了两张事件CG", "https://official.example/news", "无图#小李#", "发售日公布", "https://official.example/other"])
            result = run_prescan(source, output=output, offline=True)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result, output)
            self.assertEqual(manifest["totals"]["articles"], 2)
            self.assertEqual(manifest["articles"][0]["expected_image_count"], 2)
            self.assertEqual(manifest["articles"][1]["status"], "no_new_image")
            with (output / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(any(row["status"] == "no_new_image" for row in rows))
            self.assertIn("图片预检报告", (output / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
