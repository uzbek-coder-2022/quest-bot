from __future__ import annotations

import unittest

from quest_bot.localization import LANGUAGES, TEXTS


class LocalizationTests(unittest.TestCase):
    def test_each_message_has_uzbek_russian_and_english_text(self) -> None:
        for key, translations in TEXTS.items():
            with self.subTest(key=key):
                self.assertEqual(set(translations), set(LANGUAGES))
                self.assertTrue(all(translations[language].strip() for language in LANGUAGES))

    def test_statistics_metrics_include_emojis_in_every_language(self) -> None:
        for language in LANGUAGES:
            with self.subTest(language=language):
                stats = TEXTS["stats"][language]
                for emoji in ("👥", "🆕", "🟢", "🌐", "🛡", "🧭", "⏸️", "✅", "🏁"):
                    self.assertIn(emoji, stats)

    def test_pause_action_uses_localized_labels_without_english_in_uzbek(self) -> None:
        for key in ("btn_pause", "status_paused", "pause_success", "quest_paused_notice"):
            with self.subTest(key=key):
                self.assertNotIn("pause", TEXTS[key]["uz"].lower())
                self.assertTrue(TEXTS[key]["ru"])
                self.assertTrue(TEXTS[key]["en"])


if __name__ == "__main__":
    unittest.main()
