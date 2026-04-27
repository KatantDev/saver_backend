import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from saver_backend.services.downloaders.exceptions import KinovodQualityParseError
from saver_backend.services.downloaders.schema import (
    EpisodeDTO,
    SeasonDTO,
    VideoTrackDTO,
)
from saver_backend.telegram_bot.keyboards.callback import VideoTranslationCallback


class KinovodDTO(BaseModel):
    """Main DTO for Kinovod content."""

    title: str = ""
    url: str = ""
    seasons: List[SeasonDTO] = Field(default_factory=list)
    qualities: List[str] = Field(default_factory=list)
    perevod_from_html: Optional[str] = None

    def __init__(self, url: str, playlist_str: str, perevod_from_html: str) -> None:
        super().__init__(
            url=url,
            perevod_from_html=perevod_from_html,
            seasons=[],
            qualities=[],
        )

        self._qualities: set[str] = set()
        self._episode_title: str = ""

        seasons, qualities = self.parse(playlist_str)
        if "/film/" in self.url and len(seasons[0].episodes) > 1:
            seasons[0].episodes = self._merge_episodes(episodes=seasons[0].episodes)

        self.seasons = seasons
        self.qualities = qualities
        self._post_init()

    def get_all_episodes(self) -> List[EpisodeDTO]:
        """Return all episodes from all seasons."""
        result = []
        for season in self.seasons:
            result.extend(season.episodes)
        logging.debug(f"Retrieved {len(result)} total episodes")
        return result

    def parse(self, playlist_str: str) -> tuple[List[SeasonDTO], List[str]]:
        """Parse raw JSON string into KinovodDTO components."""
        data = self._to_dict(playlist_str)
        logging.info(f"Starting to parse {len(data)} seasons")

        seasons = []

        for season_idx, season_data in enumerate(data):
            season_title = season_data.get("title", f"Season {season_idx + 1}")
            episodes = []

            folder = season_data.get("folder", [])
            logging.debug(
                f"Parsing season '{season_title}' with {len(folder)} episodes",
            )

            for episode_data in folder:
                try:
                    episode = self._parse_episode(episode_data)
                    episodes.append(episode)
                except Exception as e:
                    logging.error(
                        f"Failed to parse episode {episode_data.get('id')}: {e}",
                    )
                    continue

            if episodes:
                try:
                    season = SeasonDTO(title=season_title, episodes=episodes)
                    seasons.append(season)
                except Exception as e:
                    logging.error(f"Failed to create season '{season_title}': {e}")
            else:
                logging.warning(f"Season '{season_title}' has no valid episodes")

        qualities = sorted(
            self._qualities,
            key=lambda x: int(x.replace("p", "")),
        )

        logging.info(f"Successfully parsed {len(seasons)} seasons")
        return seasons, qualities

    def _parse_episode(self, episode_data: Dict[str, Any]) -> EpisodeDTO:
        """Parse single episode from raw data."""
        episode_id = episode_data.get("id")
        title = episode_data.get("title", f"Episode {episode_id}")

        file_data = episode_data.get("file", "")
        if any(word in title for word in [" серия", " выпуск"]):
            part1, part2, *perevod = title.split(" ")
            perevod = perevod[0] if perevod else ""
            title = f"{part1} {part2}"
        else:
            perevod = title

        video_tracks = self._parse_video_tracks(file_data, perevod)

        logging.debug(
            f"Parsed episode {episode_id}: {title} with {len(video_tracks)} tracks",
        )

        return EpisodeDTO(id=episode_id, title=title, video_tracks=video_tracks)

    def _parse_video_tracks(
        self,
        file_data: str,
        perevod: str,
    ) -> List[VideoTrackDTO]:
        """Parse video tracks from raw file data string."""
        if not file_data:
            return []

        tracks = []
        comma_parts = file_data.split(",")

        for comma_part in comma_parts:
            _comma_part: str = ""
            if "p]" in comma_part:
                quality, _comma_part = comma_part.split("]")
                quality = quality.lstrip("[")
            else:
                logging.error(f"[kinovodDTO] no quality: {self.url}")
                raise KinovodQualityParseError

            self._qualities.add(quality)

            semicolon_parts = _comma_part.split(";")

            for semicolon_part in semicolon_parts:
                if not semicolon_part.strip():
                    continue

                try:
                    translation_name, urls = self._extract_track_data(
                        semicolon_part,
                        perevod,
                    )

                    track = VideoTrackDTO(
                        quality=quality,
                        translation=translation_name,
                        urls=urls,
                    )
                    tracks.append(track)
                except Exception as e:
                    logging.warning(
                        f"Failed to parse track part"
                        f" '{semicolon_part[:50]}...': {e} {self.url}",
                    )
                    continue

        return tracks

    def _extract_track_data(
        self,
        semicolon_part: str,
        perevod: str,
    ) -> tuple[str, List[str]]:
        """Extract translation and URLs from a track part."""
        # Extract translation
        if semicolon_part.startswith("{"):
            # Has translation in curly braces
            translation_name, urls_part = semicolon_part.split("}")
            translation_name = translation_name.lstrip("{").strip()
        else:
            urls_part = semicolon_part
            if perevod:
                translation_name = perevod
            elif "," not in (self.perevod_from_html or ""):
                translation_name = self.perevod_from_html or ""
            else:
                translation_name = ""
                logging.warning(f"[kinovod schema] translation parse: {self.url}")

        # Parse URLs
        urls = [url.strip() for url in urls_part.split(" or ") if url.strip()]

        if not urls:
            raise ValueError("No valid URLs found")

        return translation_name, urls

    @staticmethod
    def _to_dict(playlist_str: str) -> list[dict[str, Any]]:
        """
        Parse raw string into dict object.

        Args:
            playlist_str: JSON string or raw playlist data
        """
        try:
            playlist = json.loads(playlist_str)
        except json.decoder.JSONDecodeError:
            playlist = [
                {
                    "title": "1 сезон",
                    "folder": [{"title": "1 серия", "file": playlist_str}],
                },
            ]

        # Handle single episode without folder structure
        if "folder" not in playlist[0]:
            _playlist: list[dict[str, Any]] = [{"title": "1 сезон", "folder": []}]
            for episode in playlist:
                _playlist[0]["folder"].append(episode)
            playlist = _playlist

        return playlist

    @staticmethod
    def _get_crc_32(text: str) -> str:
        import zlib

        """Returns a deterministic 32-bit hash for a string."""
        hash_int = zlib.crc32(text.encode("utf-8"))
        return str(hash_int & 0xFFFFFFFF)

    def _normalize_translation_key(self, translation_name: str) -> str:
        """Normalize length of translation key."""
        if not translation_name:
            return "Unknown"

        prefix = VideoTranslationCallback.__prefix__
        encoded_len = len(f"{prefix}:{translation_name}".encode())
        if encoded_len > 64:
            _translation = re.sub(
                r'[^A-Za-z0-9\s!@#$%^&*()_+\-=\[\]{};:\'",.<>/?\\|`~]',
                "",
                translation_name,
            ).strip()

            pattern = r'^[0-9\s!@#$%^&*()_+\-=\[\]{};:\'",.<>/?\\|`~]+$'

            if not _translation or bool(re.match(pattern, _translation)):
                _translation = self._get_crc_32(translation_name)
        else:
            _translation = translation_name
        return _translation.lower()

    @staticmethod
    def _merge_episodes(episodes: list[EpisodeDTO]) -> list[EpisodeDTO]:
        """Merge seasons episodes into season list with one item."""
        result: list[VideoTrackDTO] = []
        for episode in episodes:
            for track in episode.video_tracks:
                result.append(track)

        ep = EpisodeDTO()
        ep.video_tracks = result
        return [ep]

    def _post_init(self) -> None:
        """Validate and log after initialization."""
        total_episodes = len(self.get_all_episodes())
        logging.info(
            f"KinovodDTO initialized: {len(self.seasons)} seasons,"
            f" {total_episodes} episodes",
        )

        if not self.seasons:
            logging.warning("KinovodDTO created with no seasons")
