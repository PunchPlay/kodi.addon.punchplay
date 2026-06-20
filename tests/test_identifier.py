from __future__ import annotations

import importlib
import os
import sys
import types
import unittest

LIB_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "resources", "lib")
)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

if "xbmc" not in sys.modules:
    sys.modules["xbmc"] = types.SimpleNamespace(
        LOGDEBUG=0,
        log=lambda *args, **kwargs: None,
    )

identifier = importlib.import_module("identifier")


class RegexGuessTests(unittest.TestCase):
    def test_parser_handles_common_movie_tv_and_anime_patterns(self) -> None:
        cases = [
            (
                "/Movies/Inception (2010)/Inception.2010.1080p.BluRay.mkv",
                {"media_type": "movie", "title": "Inception", "year": 2010},
            ),
            (
                "/Movies/Inception (2010)/Inception.mkv",
                {"media_type": "movie", "title": "Inception", "year": 2010},
            ),
            (
                "/Movies/The.Matrix.1999.REMASTERED.1080p.mkv",
                {"media_type": "movie", "title": "The Matrix", "year": 1999},
            ),
            (
                "/Movies/Movie.Title.2023.WEB-DL.x265-GROUP.mkv",
                {"media_type": "movie", "title": "Movie Title", "year": 2023},
            ),
            (
                "/Movies/Movie Title [2023] [1080p].mkv",
                {"media_type": "movie", "title": "Movie Title", "year": 2023},
            ),
            (
                "/TV/Breaking Bad/Season 01/Breaking.Bad.S01E02.mkv",
                {"media_type": "episode", "title": "Breaking Bad", "season": 1, "episode": 2},
            ),
            (
                "/TV/Breaking Bad/Breaking Bad - S01E02 - Cat's in the Bag.mkv",
                {
                    "media_type": "episode",
                    "title": "Breaking Bad",
                    "season": 1,
                    "episode": 2,
                    "episode_title": "Cat's in the Bag",
                },
            ),
            (
                "/TV/Breaking Bad/Breaking.Bad.1x02.mkv",
                {"media_type": "episode", "title": "Breaking Bad", "season": 1, "episode": 2},
            ),
            (
                "/TV/Show Name/Season 01/Show.Name.S01E01E02.mkv",
                {
                    "media_type": "episode",
                    "title": "Show Name",
                    "season": 1,
                    "episode": 1,
                    "episode_end": 2,
                },
            ),
            (
                "/TV/Show Name/Season 01/Show.Name.S01E01-E02.mkv",
                {
                    "media_type": "episode",
                    "title": "Show Name",
                    "season": 1,
                    "episode": 1,
                    "episode_end": 2,
                },
            ),
            (
                "/TV/Show Name/Season 01/Show.Name.S01E01-E03.mkv",
                {
                    "media_type": "episode",
                    "title": "Show Name",
                    "season": 1,
                    "episode": 1,
                    "episode_end": 3,
                },
            ),
            (
                "/TV/Show Name/Season 01/Show.1x02x03.mkv",
                {
                    "media_type": "episode",
                    "title": "Show",
                    "season": 1,
                    "episode": 2,
                    "episode_end": 3,
                },
            ),
            (
                "/TV/The Office/Season 2/02 - Sexual Harassment.mkv",
                {
                    "media_type": "episode",
                    "title": "The Office",
                    "season": 2,
                    "episode": 2,
                    "episode_title": "Sexual Harassment",
                },
            ),
            (
                "/TV/The Office/S02/02.mkv",
                {
                    "media_type": "episode",
                    "title": "The Office",
                    "season": 2,
                    "episode": 2,
                },
            ),
            (
                "/TV/The Office/Season 02/The Office - 02 - Sexual Harassment.mkv",
                {
                    "media_type": "episode",
                    "title": "The Office",
                    "season": 2,
                    "episode": 2,
                    "episode_title": "Sexual Harassment",
                },
            ),
            (
                "/TV/Show Name/Show Name Episode 02.mkv",
                {
                    "media_type": "episode",
                    "title": "Show Name",
                    "episode": 2,
                },
            ),
            (
                "/Anime/Frieren Beyond Journey's End/[SubsPlease] Sousou no Frieren - 07 (1080p).mkv",
                {
                    "media_type": "episode",
                    "title": "Sousou no Frieren",
                    "absolute_episode": 7,
                    "anime": True,
                },
            ),
            (
                "/Anime/Show Name/[Group] Show Name - 07 [1080p][ABC123].mkv",
                {
                    "media_type": "episode",
                    "title": "Show Name",
                    "absolute_episode": 7,
                    "anime": True,
                },
            ),
            (
                "/Anime/Show Name/Show Name - 007.mkv",
                {
                    "media_type": "episode",
                    "title": "Show Name",
                    "absolute_episode": 7,
                    "anime": True,
                },
            ),
            (
                "/Anime/Show Name/Show Name Episode 07.mkv",
                {
                    "media_type": "episode",
                    "title": "Show Name",
                    "episode": 7,
                    "anime": True,
                },
            ),
            (
                "/Anime/One Piece/One Piece - 1089.mkv",
                {
                    "media_type": "episode",
                    "title": "One Piece",
                    "absolute_episode": 1089,
                    "anime": True,
                },
            ),
            (
                "/Anime/Demon Slayer/Season 3/Demon Slayer - S03E04.mkv",
                {
                    "media_type": "episode",
                    "title": "Demon Slayer",
                    "season": 3,
                    "episode": 4,
                },
            ),
            (
                "/TV/Room 104/Season 01/Room.104.S01E01.mkv",
                {
                    "media_type": "episode",
                    "title": "Room 104",
                    "season": 1,
                    "episode": 1,
                },
            ),
            (
                "/Movies/Spider-Man.No.Way.Home.2021.2160p.WEB-DL.DV.Atmos.mkv",
                {
                    "media_type": "movie",
                    "title": "Spider-Man No Way Home",
                    "year": 2021,
                },
            ),
            (
                "/TV/Show/Season 01/01 - Pilot [1080p].mkv",
                {
                    "media_type": "episode",
                    "title": "Show",
                    "season": 1,
                    "episode": 1,
                    "episode_title": "Pilot",
                },
            ),
        ]

        for path, expected in cases:
            with self.subTest(path=path):
                result = identifier._regex_guess(path)  # pylint: disable=protected-access
                for key, value in expected.items():
                    self.assertEqual(result.get(key), value)

    def test_anime_absolute_preference_can_be_disabled(self) -> None:
        result = identifier._regex_guess(
            "/Anime/Show Name/Show Name - 007.mkv",
            anime_preference="season_episode",
        )
        self.assertEqual(result["media_type"], "movie")

    def test_anime_detection_ignores_animation_genre(self) -> None:
        class _InfoTag:
            def getGenres(self) -> list[str]:
                return ["Animation"]

        self.assertFalse(identifier.is_anime(_InfoTag()))


if __name__ == "__main__":
    unittest.main()
