"""
Deezer MCP Server
Provides search, retrieval, and exploration of music content via the Deezer API.
Also integrates Last.fm for personalized listening data and track management,
and Windows SMTC for real-time playback control and now-playing info.
"""

import asyncio
import hashlib
import logging
import os
import sys
import time
from typing import Dict, Any, Optional
import aiohttp
from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator
import json

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Windows SMTC optional import ─────────────────────────────────────────────
SMTC_AVAILABLE = False
if sys.platform == "win32":
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as _SMTCManager,
        )
        from winrt.windows.media import MediaPlaybackAutoRepeatMode as _RepeatMode
        SMTC_AVAILABLE = True
    except ImportError:
        logger.warning("winrt-Windows.Media.Control not installed — SMTC tools unavailable")

mcp = FastMCP("Deezer Music Server")

BASE_URL = "https://api.deezer.com"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Last.fm configuration (read from environment)
LASTFM_BASE_URL = "https://ws.audioscrobbler.com/2.0/"
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
LASTFM_API_SECRET = os.environ.get("LASTFM_API_SECRET", "")
LASTFM_USERNAME = os.environ.get("LASTFM_USERNAME", "")
LASTFM_SESSION_KEY = os.environ.get("LASTFM_SESSION_KEY", "")

VALID_ORDERS = [
    "RANKING", "TRACK_ASC", "TRACK_DESC", "ARTIST_ASC", "ARTIST_DESC",
    "ALBUM_ASC", "ALBUM_DESC", "RATING_ASC", "RATING_DESC",
    "DURATION_ASC", "DURATION_DESC"
]


class DeezerAPIError(Exception):
    pass


class SearchParams(BaseModel):
    query: str = Field(..., description="Search term")
    limit: int = Field(default=10, ge=1, le=25, description="Number of results to return (max 25)")
    strict: bool = Field(default=False, description="Enable strict (non-fuzzy) matching")
    order: str = Field(default="RANKING", description="Sort order for results")

    @field_validator('order')
    @classmethod
    def validate_order(cls, v):
        if v not in VALID_ORDERS:
            raise ValueError(f"Order must be one of {VALID_ORDERS}")
        return v


class AdvancedSearchParams(BaseModel):
    artist: Optional[str] = Field(None, description="Artist name")
    album: Optional[str] = Field(None, description="Album title")
    track: Optional[str] = Field(None, description="Track title")
    label: Optional[str] = Field(None, description="Record label name")
    dur_min: Optional[int] = Field(None, ge=0, description="Minimum duration in seconds")
    dur_max: Optional[int] = Field(None, ge=0, description="Maximum duration in seconds")
    bpm_min: Optional[int] = Field(None, ge=0, description="Minimum BPM")
    bpm_max: Optional[int] = Field(None, ge=0, description="Maximum BPM")
    limit: int = Field(default=10, ge=1, le=25, description="Number of results (max 25)")
    strict: bool = Field(default=False, description="Strict matching mode")
    order: str = Field(default="RANKING", description="Sort order")

    @field_validator('order')
    @classmethod
    def validate_order(cls, v):
        if v not in VALID_ORDERS:
            raise ValueError(f"Order must be one of {VALID_ORDERS}")
        return v


async def make_api_request(session: aiohttp.ClientSession, endpoint: str, params: Dict = None) -> Dict[str, Any]:
    """Make a request to the Deezer API with error handling."""
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    try:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                if "error" in data:
                    raise DeezerAPIError(f"API Error: {data['error']}")
                return data
            else:
                raise DeezerAPIError(f"HTTP {response.status}: {await response.text()}")
    except asyncio.TimeoutError:
        raise DeezerAPIError(f"Request to {endpoint} timed out")
    except aiohttp.ClientError as e:
        raise DeezerAPIError(f"Request failed: {str(e)}")


@mcp.tool()
async def search_tracks(params: SearchParams) -> Dict[str, Any]:
    """
    Search for music tracks on Deezer.

    Args:
        params: Search parameters including query string, limit (max 25), sort order,
                and strict mode flag.

    Returns:
        Dict with search results containing track list and metadata.

    Example:
        search_tracks({"query": "eminem lose yourself", "limit": 10})
    """
    search_params = {
        "q": params.query,
        "limit": params.limit,
    }
    if params.strict:
        search_params["strict"] = "on"
    if params.order != "RANKING":
        search_params["order"] = params.order

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, "search", search_params)
            return {
                "success": True,
                "query": params.query,
                "total": result.get("total", 0),
                "tracks": result.get("data", []),
                "next": result.get("next"),
                "prev": result.get("prev"),
            }
        except DeezerAPIError as e:
            logger.error(f"Search error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def advanced_search(params: AdvancedSearchParams) -> Dict[str, Any]:
    """
    Search Deezer with specific criteria like artist, BPM range, duration, and label.

    Args:
        params: Advanced search parameters. At least one criterion is required.
                Supports: artist, album, track, label, dur_min, dur_max, bpm_min, bpm_max.

    Returns:
        Dict with filtered search results.

    Example:
        advanced_search({"artist": "daft punk", "bpm_min": 120, "dur_min": 180})
    """
    query_parts = []

    if params.artist:
        query_parts.append(f'artist:"{params.artist}"')
    if params.album:
        query_parts.append(f'album:"{params.album}"')
    if params.track:
        query_parts.append(f'track:"{params.track}"')
    if params.label:
        query_parts.append(f'label:"{params.label}"')
    if params.dur_min:
        query_parts.append(f'dur_min:{params.dur_min}')
    if params.dur_max:
        query_parts.append(f'dur_max:{params.dur_max}')
    if params.bpm_min:
        query_parts.append(f'bpm_min:{params.bpm_min}')
    if params.bpm_max:
        query_parts.append(f'bpm_max:{params.bpm_max}')

    if not query_parts:
        return {"success": False, "error": "At least one search criterion is required"}

    query = " ".join(query_parts)
    search_params = {"q": query, "limit": params.limit}
    if params.strict:
        search_params["strict"] = "on"
    if params.order != "RANKING":
        search_params["order"] = params.order

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, "search", search_params)
            return {
                "success": True,
                "query": query,
                "criteria": {k: v for k, v in params.model_dump().items() if v is not None and k not in ['limit', 'strict', 'order']},
                "total": result.get("total", 0),
                "tracks": result.get("data", []),
                "next": result.get("next"),
                "prev": result.get("prev"),
            }
        except DeezerAPIError as e:
            logger.error(f"Advanced search error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_track_details(track_id: int) -> Dict[str, Any]:
    """
    Get complete details for a specific track by its Deezer ID.

    Args:
        track_id: The Deezer track ID.

    Returns:
        Dict with full track info including title, artist, album, duration, preview URL, and BPM.

    Example:
        get_track_details(3135556)
    """
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"track/{track_id}")
            return {"success": True, "track": result}
        except DeezerAPIError as e:
            logger.error(f"Track details error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_artist_details(artist_id: int) -> Dict[str, Any]:
    """
    Get details for a specific artist by their Deezer ID.

    Args:
        artist_id: The Deezer artist ID.

    Returns:
        Dict with artist info including name, fan count, and picture URLs.

    Example:
        get_artist_details(27)  # Daft Punk
    """
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"artist/{artist_id}")
            return {"success": True, "artist": result}
        except DeezerAPIError as e:
            logger.error(f"Artist details error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_artist_albums(artist_id: int, limit: int = 25) -> Dict[str, Any]:
    """
    Get the discography (albums) for a specific artist.

    Args:
        artist_id: The Deezer artist ID.
        limit: Number of albums to retrieve (default 25, max 100).

    Returns:
        Dict with list of albums including release dates and track counts.
    """
    limit = max(1, min(limit, 100))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"artist/{artist_id}/albums", {"limit": limit})
            return {
                "success": True,
                "artist_id": artist_id,
                "total": result.get("total", 0),
                "albums": result.get("data", []),
            }
        except DeezerAPIError as e:
            logger.error(f"Artist albums error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_artist_top_tracks(artist_id: int, limit: int = 10) -> Dict[str, Any]:
    """
    Get the most popular tracks for a specific artist.

    Args:
        artist_id: The Deezer artist ID.
        limit: Number of top tracks to retrieve (default 10, max 100).

    Returns:
        Dict with the artist's top tracks ranked by popularity.
    """
    limit = max(1, min(limit, 100))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"artist/{artist_id}/top", {"limit": limit})
            return {
                "success": True,
                "artist_id": artist_id,
                "total": result.get("total", 0),
                "top_tracks": result.get("data", []),
            }
        except DeezerAPIError as e:
            logger.error(f"Artist top tracks error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_artist_related(artist_id: int, limit: int = 10) -> Dict[str, Any]:
    """
    Get artists related to (similar to) a specific artist.

    Args:
        artist_id: The Deezer artist ID.
        limit: Number of related artists to retrieve (default 10, max 100).

    Returns:
        Dict with list of similar/related artists.
    """
    limit = max(1, min(limit, 100))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"artist/{artist_id}/related", {"limit": limit})
            return {
                "success": True,
                "artist_id": artist_id,
                "total": result.get("total", 0),
                "related_artists": result.get("data", []),
            }
        except DeezerAPIError as e:
            logger.error(f"Artist related error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_album_details(album_id: int) -> Dict[str, Any]:
    """
    Get complete details for a specific album by its Deezer ID.

    Args:
        album_id: The Deezer album ID.

    Returns:
        Dict with album info including title, artist, tracklist, release date, and genre.

    Example:
        get_album_details(302127)
    """
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"album/{album_id}")
            return {"success": True, "album": result}
        except DeezerAPIError as e:
            logger.error(f"Album details error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_album_tracks(album_id: int) -> Dict[str, Any]:
    """
    Get the full tracklist for a specific album.

    Args:
        album_id: The Deezer album ID.

    Returns:
        Dict with all tracks in the album including track numbers, titles, and durations.

    Example:
        get_album_tracks(302127)
    """
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"album/{album_id}/tracks")
            return {
                "success": True,
                "album_id": album_id,
                "total": result.get("total", 0),
                "tracks": result.get("data", []),
            }
        except DeezerAPIError as e:
            logger.error(f"Album tracks error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_playlist_details(playlist_id: int) -> Dict[str, Any]:
    """
    Get complete details for a specific playlist by its Deezer ID.

    Args:
        playlist_id: The Deezer playlist ID.

    Returns:
        Dict with playlist info including title, creator, track count, and tracklist.

    Example:
        get_playlist_details(908622995)
    """
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"playlist/{playlist_id}")
            return {"success": True, "playlist": result}
        except DeezerAPIError as e:
            logger.error(f"Playlist details error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def search_artists(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    Search for artists on Deezer by name.

    Args:
        query: Artist name or search term.
        limit: Number of results to return (default 10, max 25).

    Returns:
        Dict with matching artists including their IDs, names, and picture URLs.
    """
    limit = max(1, min(limit, 25))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, "search/artist", {"q": query, "limit": limit})
            return {
                "success": True,
                "query": query,
                "total": result.get("total", 0),
                "artists": result.get("data", []),
            }
        except DeezerAPIError as e:
            logger.error(f"Artist search error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def search_albums(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    Search for albums on Deezer by title or artist.

    Args:
        query: Album title or artist name to search for.
        limit: Number of results to return (default 10, max 25).

    Returns:
        Dict with matching albums including titles, artists, and cover art URLs.
    """
    limit = max(1, min(limit, 25))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, "search/album", {"q": query, "limit": limit})
            return {
                "success": True,
                "query": query,
                "total": result.get("total", 0),
                "albums": result.get("data", []),
            }
        except DeezerAPIError as e:
            logger.error(f"Album search error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def search_playlists(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    Search for public playlists on Deezer.

    Args:
        query: Playlist title or theme to search for.
        limit: Number of results to return (default 10, max 25).

    Returns:
        Dict with matching playlists including titles, track counts, and creator info.
    """
    limit = max(1, min(limit, 25))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, "search/playlist", {"q": query, "limit": limit})
            return {
                "success": True,
                "query": query,
                "total": result.get("total", 0),
                "playlists": result.get("data", []),
            }
        except DeezerAPIError as e:
            logger.error(f"Playlist search error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_genre_list() -> Dict[str, Any]:
    """
    Get the list of all available music genres on Deezer.

    Returns:
        Dict with all genre IDs and names (e.g., Pop, Rock, Hip-Hop, Electronic, etc.)
    """
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, "genre")
            return {"success": True, "genres": result.get("data", [])}
        except DeezerAPIError as e:
            logger.error(f"Genres error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_genre_artists(genre_id: int, limit: int = 25) -> Dict[str, Any]:
    """
    Get popular artists within a specific music genre.

    Args:
        genre_id: The Deezer genre ID (use get_genre_list to find IDs).
        limit: Number of artists to retrieve (default 25, max 100).

    Returns:
        Dict with artists associated with the genre.
    """
    limit = max(1, min(limit, 100))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"genre/{genre_id}/artists", {"limit": limit})
            return {
                "success": True,
                "genre_id": genre_id,
                "total": result.get("total", 0),
                "artists": result.get("data", []),
            }
        except DeezerAPIError as e:
            logger.error(f"Genre artists error: {e}")
            return {"success": False, "error": str(e)}


@mcp.tool()
async def get_chart(genre_id: int = 0, limit: int = 10) -> Dict[str, Any]:
    """
    Get the current Deezer music charts (top tracks, albums, artists, and playlists).

    Args:
        genre_id: Genre ID to filter charts by (default 0 = all genres).
                  Use get_genre_list to find genre IDs.
        limit: Number of entries per chart category (default 10, max 50).

    Returns:
        Dict with top tracks, albums, artists, and playlists charts.
    """
    limit = max(1, min(limit, 50))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            result = await make_api_request(session, f"chart/{genre_id}", {"limit": limit})
            return {
                "success": True,
                "genre_id": genre_id,
                "tracks": result.get("tracks", {}).get("data", []),
                "albums": result.get("albums", {}).get("data", []),
                "artists": result.get("artists", {}).get("data", []),
                "playlists": result.get("playlists", {}).get("data", []),
            }
        except DeezerAPIError as e:
            logger.error(f"Chart error: {e}")
            return {"success": False, "error": str(e)}


# ── Last.fm helpers ──────────────────────────────────────────────────────────

def _lastfm_sign(params: Dict[str, str]) -> str:
    """Compute Last.fm API signature: MD5 of sorted key+value pairs + secret."""
    pairs = "".join(f"{k}{v}" for k, v in sorted(params.items()) if k not in ("format", "callback"))
    return hashlib.md5((pairs + LASTFM_API_SECRET).encode("utf-8")).hexdigest()


async def lastfm_get(session: aiohttp.ClientSession, method: str, extra: Dict = None) -> Dict[str, Any]:
    """Make an authenticated GET request to the Last.fm API."""
    if not LASTFM_API_KEY:
        raise ValueError("LASTFM_API_KEY environment variable is not set")
    params = {"method": method, "api_key": LASTFM_API_KEY, "format": "json"}
    if extra:
        params.update(extra)
    async with session.get(LASTFM_BASE_URL, params=params) as response:
        data = await response.json()
        if "error" in data:
            raise ValueError(f"Last.fm error {data['error']}: {data.get('message', '')}")
        return data


async def lastfm_post(session: aiohttp.ClientSession, method: str, extra: Dict = None) -> Dict[str, Any]:
    """Make a signed POST request to the Last.fm write API."""
    if not LASTFM_API_KEY or not LASTFM_API_SECRET:
        raise ValueError("LASTFM_API_KEY and LASTFM_API_SECRET must be set for write operations")
    if not LASTFM_SESSION_KEY:
        raise ValueError("LASTFM_SESSION_KEY must be set for write operations — run lastfm_auth.py to generate one")
    params = {"method": method, "api_key": LASTFM_API_KEY, "sk": LASTFM_SESSION_KEY}
    if extra:
        params.update(extra)
    params["api_sig"] = _lastfm_sign(params)
    params["format"] = "json"
    async with session.post(LASTFM_BASE_URL, data=params) as response:
        data = await response.json()
        if "error" in data:
            raise ValueError(f"Last.fm error {data['error']}: {data.get('message', '')}")
        return data


# ── Last.fm read tools ────────────────────────────────────────────────────────

@mcp.tool()
async def lastfm_get_now_playing(username: str = "") -> Dict[str, Any]:
    """
    Get the track currently playing (or most recently played) for a Last.fm user.

    Requires Deezer (or any player) to be scrobbling to Last.fm. The response
    includes a 'now_playing' boolean flag.

    Args:
        username: Last.fm username. Defaults to LASTFM_USERNAME env var if not provided.

    Returns:
        Dict with track title, artist, album, and whether it is currently playing.
    """
    user = username or LASTFM_USERNAME
    if not user:
        return {"success": False, "error": "No username provided and LASTFM_USERNAME is not set"}
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            data = await lastfm_get(session, "user.getRecentTracks", {"user": user, "limit": "1", "extended": "1"})
            tracks = data.get("recenttracks", {}).get("track", [])
            if not tracks:
                return {"success": True, "now_playing": False, "track": None}
            track = tracks[0]
            now_playing = track.get("@attr", {}).get("nowplaying") == "true"
            return {
                "success": True,
                "now_playing": now_playing,
                "track": {
                    "title": track.get("name"),
                    "artist": track.get("artist", {}).get("name") or track.get("artist", {}).get("#text"),
                    "album": track.get("album", {}).get("#text"),
                    "url": track.get("url"),
                    "image": next((i["#text"] for i in track.get("image", []) if i["size"] == "large"), None),
                    "loved": track.get("loved") == "1",
                },
            }
        except ValueError as e:
            return {"success": False, "error": str(e)}


@mcp.tool()
async def lastfm_get_recent_tracks(username: str = "", limit: int = 10) -> Dict[str, Any]:
    """
    Get the recent listening history for a Last.fm user.

    Args:
        username: Last.fm username. Defaults to LASTFM_USERNAME env var.
        limit: Number of tracks to return (default 10, max 50).

    Returns:
        Dict with list of recently played tracks including timestamps.
    """
    user = username or LASTFM_USERNAME
    if not user:
        return {"success": False, "error": "No username provided and LASTFM_USERNAME is not set"}
    limit = max(1, min(limit, 50))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            data = await lastfm_get(session, "user.getRecentTracks", {"user": user, "limit": str(limit)})
            raw = data.get("recenttracks", {}).get("track", [])
            tracks = []
            for t in raw:
                tracks.append({
                    "title": t.get("name"),
                    "artist": t.get("artist", {}).get("#text"),
                    "album": t.get("album", {}).get("#text"),
                    "now_playing": t.get("@attr", {}).get("nowplaying") == "true",
                    "played_at": t.get("date", {}).get("#text"),
                    "url": t.get("url"),
                })
            return {"success": True, "username": user, "tracks": tracks}
        except ValueError as e:
            return {"success": False, "error": str(e)}


LASTFM_PERIODS = ["overall", "7day", "1month", "3month", "6month", "12month"]


@mcp.tool()
async def lastfm_get_top_tracks(username: str = "", period: str = "1month", limit: int = 10) -> Dict[str, Any]:
    """
    Get a user's most played tracks on Last.fm over a given time period.

    Args:
        username: Last.fm username. Defaults to LASTFM_USERNAME env var.
        period: Time period — one of: overall, 7day, 1month, 3month, 6month, 12month.
        limit: Number of tracks (default 10, max 50).

    Returns:
        Dict with ranked list of top tracks and play counts.
    """
    user = username or LASTFM_USERNAME
    if not user:
        return {"success": False, "error": "No username provided and LASTFM_USERNAME is not set"}
    if period not in LASTFM_PERIODS:
        return {"success": False, "error": f"period must be one of {LASTFM_PERIODS}"}
    limit = max(1, min(limit, 50))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            data = await lastfm_get(session, "user.getTopTracks", {"user": user, "period": period, "limit": str(limit)})
            raw = data.get("toptracks", {}).get("track", [])
            tracks = [{"rank": t.get("@attr", {}).get("rank"), "title": t.get("name"),
                       "artist": t.get("artist", {}).get("name"), "play_count": t.get("playcount"),
                       "url": t.get("url")} for t in raw]
            return {"success": True, "username": user, "period": period, "tracks": tracks}
        except ValueError as e:
            return {"success": False, "error": str(e)}


@mcp.tool()
async def lastfm_get_top_artists(username: str = "", period: str = "1month", limit: int = 10) -> Dict[str, Any]:
    """
    Get a user's most played artists on Last.fm over a given time period.

    Args:
        username: Last.fm username. Defaults to LASTFM_USERNAME env var.
        period: Time period — one of: overall, 7day, 1month, 3month, 6month, 12month.
        limit: Number of artists (default 10, max 50).

    Returns:
        Dict with ranked list of top artists and play counts.
    """
    user = username or LASTFM_USERNAME
    if not user:
        return {"success": False, "error": "No username provided and LASTFM_USERNAME is not set"}
    if period not in LASTFM_PERIODS:
        return {"success": False, "error": f"period must be one of {LASTFM_PERIODS}"}
    limit = max(1, min(limit, 50))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            data = await lastfm_get(session, "user.getTopArtists", {"user": user, "period": period, "limit": str(limit)})
            raw = data.get("topartists", {}).get("artist", [])
            artists = [{"rank": a.get("@attr", {}).get("rank"), "name": a.get("name"),
                        "play_count": a.get("playcount"), "url": a.get("url")} for a in raw]
            return {"success": True, "username": user, "period": period, "artists": artists}
        except ValueError as e:
            return {"success": False, "error": str(e)}


@mcp.tool()
async def lastfm_get_loved_tracks(username: str = "", limit: int = 20) -> Dict[str, Any]:
    """
    Get the tracks a user has loved (hearted) on Last.fm.

    Args:
        username: Last.fm username. Defaults to LASTFM_USERNAME env var.
        limit: Number of loved tracks to return (default 20, max 50).

    Returns:
        Dict with list of loved tracks and the date they were loved.
    """
    user = username or LASTFM_USERNAME
    if not user:
        return {"success": False, "error": "No username provided and LASTFM_USERNAME is not set"}
    limit = max(1, min(limit, 50))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            data = await lastfm_get(session, "user.getLovedTracks", {"user": user, "limit": str(limit)})
            raw = data.get("lovedtracks", {}).get("track", [])
            tracks = [{"title": t.get("name"), "artist": t.get("artist", {}).get("name"),
                       "loved_at": t.get("date", {}).get("#text"), "url": t.get("url")} for t in raw]
            return {"success": True, "username": user, "tracks": tracks}
        except ValueError as e:
            return {"success": False, "error": str(e)}


@mcp.tool()
async def lastfm_get_similar_artists(artist: str, limit: int = 10) -> Dict[str, Any]:
    """
    Get artists similar to a given artist according to Last.fm's similarity graph.

    No authentication required — uses public Last.fm data.

    Args:
        artist: Artist name to find similar artists for.
        limit: Number of similar artists to return (default 10, max 30).

    Returns:
        Dict with similar artists ranked by similarity score.
    """
    limit = max(1, min(limit, 30))
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            data = await lastfm_get(session, "artist.getSimilar", {"artist": artist, "limit": str(limit)})
            raw = data.get("similarartists", {}).get("artist", [])
            artists = [{"name": a.get("name"), "similarity": float(a.get("match", 0)),
                        "url": a.get("url")} for a in raw]
            return {"success": True, "artist": artist, "similar_artists": artists}
        except ValueError as e:
            return {"success": False, "error": str(e)}


@mcp.tool()
async def lastfm_get_track_info(artist: str, track: str, username: str = "") -> Dict[str, Any]:
    """
    Get detailed info for a track from Last.fm including tags, wiki, and user play count.

    Args:
        artist: Artist name.
        track: Track title.
        username: Last.fm username to include personal play count and loved status.
                  Defaults to LASTFM_USERNAME env var.

    Returns:
        Dict with track metadata, tags, wiki summary, and optional user stats.
    """
    user = username or LASTFM_USERNAME
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            extra: Dict[str, str] = {"artist": artist, "track": track}
            if user:
                extra["username"] = user
            data = await lastfm_get(session, "track.getInfo", extra)
            t = data.get("track", {})
            return {
                "success": True,
                "title": t.get("name"),
                "artist": t.get("artist", {}).get("name"),
                "album": t.get("album", {}).get("title"),
                "duration_ms": t.get("duration"),
                "play_count": t.get("playcount"),
                "listeners": t.get("listeners"),
                "loved": t.get("userloved") == "1",
                "user_play_count": t.get("userplaycount"),
                "tags": [tag["name"] for tag in t.get("toptags", {}).get("tag", [])],
                "wiki": t.get("wiki", {}).get("summary", "").split("<a")[0].strip() or None,
                "url": t.get("url"),
            }
        except ValueError as e:
            return {"success": False, "error": str(e)}


# ── Last.fm write tools ───────────────────────────────────────────────────────

@mcp.tool()
async def lastfm_love_track(artist: str, track: str) -> Dict[str, Any]:
    """
    Love (heart) a track on Last.fm. Requires write authentication (LASTFM_SESSION_KEY).

    Args:
        artist: Artist name.
        track: Track title.

    Returns:
        Dict indicating success or failure.
    """
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            await lastfm_post(session, "track.love", {"artist": artist, "track": track})
            return {"success": True, "loved": True, "artist": artist, "track": track}
        except ValueError as e:
            return {"success": False, "error": str(e)}


@mcp.tool()
async def lastfm_unlove_track(artist: str, track: str) -> Dict[str, Any]:
    """
    Remove the love (heart) from a track on Last.fm. Requires write authentication.

    Args:
        artist: Artist name.
        track: Track title.

    Returns:
        Dict indicating success or failure.
    """
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            await lastfm_post(session, "track.unlove", {"artist": artist, "track": track})
            return {"success": True, "loved": False, "artist": artist, "track": track}
        except ValueError as e:
            return {"success": False, "error": str(e)}


@mcp.tool()
async def lastfm_scrobble(artist: str, track: str, album: str = "", timestamp: int = 0) -> Dict[str, Any]:
    """
    Submit a scrobble (played track record) to Last.fm. Requires write authentication.

    Args:
        artist: Artist name.
        track: Track title.
        album: Album title (optional but recommended).
        timestamp: Unix timestamp of when the track was played. Defaults to now.

    Returns:
        Dict indicating whether the scrobble was accepted.
    """
    ts = str(timestamp or int(time.time()))
    extra: Dict[str, str] = {"artist": artist, "track": track, "timestamp": ts}
    if album:
        extra["album"] = album
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            data = await lastfm_post(session, "track.scrobble", extra)
            scrobbles = data.get("scrobbles", {}).get("scrobble", {})
            accepted = scrobbles.get("ignoredMessage", {}).get("code") == "0" or "artist" in scrobbles
            return {"success": True, "accepted": accepted, "artist": artist, "track": track}
        except ValueError as e:
            return {"success": False, "error": str(e)}


@mcp.tool()
async def lastfm_update_now_playing(artist: str, track: str, album: str = "", duration: int = 0) -> Dict[str, Any]:
    """
    Tell Last.fm a track is currently playing. Requires write authentication.

    Use this to manually push a "now playing" status — useful if your player
    doesn't scrobble automatically.

    Args:
        artist: Artist name.
        track: Track title.
        album: Album title (optional but recommended).
        duration: Track duration in seconds (optional).

    Returns:
        Dict indicating success.
    """
    extra: Dict[str, str] = {"artist": artist, "track": track}
    if album:
        extra["album"] = album
    if duration:
        extra["duration"] = str(duration)
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        try:
            await lastfm_post(session, "track.updateNowPlaying", extra)
            return {"success": True, "artist": artist, "track": track}
        except ValueError as e:
            return {"success": False, "error": str(e)}


# ── SMTC helpers ─────────────────────────────────────────────────────────────

_SMTC_NOT_AVAILABLE = {"success": False, "error": "SMTC requires Windows and: pip install winrt-Windows.Media.Control winrt-Windows.Media"}

_PLAYBACK_STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
_REPEAT_NAMES = {"none": 0, "track": 1, "list": 2}
_REPEAT_VALUES = {0: "none", 1: "track", 2: "list"}
_SMTC_TIMEOUT = 5.0  # seconds before giving up on a WinRT async call


async def _smtc_session():
    """Return the current SMTC media session, or None if nothing is playing."""
    manager = await asyncio.wait_for(_SMTCManager.request_async(), timeout=_SMTC_TIMEOUT)
    return manager.get_current_session()


# ── SMTC read tools ───────────────────────────────────────────────────────────

@mcp.tool()
async def smtc_get_now_playing() -> Dict[str, Any]:
    """
    Get the track currently playing on this Windows machine via System Media Transport Controls.

    Works with any media app that registers with SMTC: Deezer, Spotify, browser,
    Windows Media Player, etc.

    Returns:
        Dict with title, artist, album, playback status, position, duration,
        shuffle state, repeat mode, and the source application name.
    """
    if not SMTC_AVAILABLE:
        return _SMTC_NOT_AVAILABLE
    try:
        session = await _smtc_session()
        if not session:
            return {"success": True, "playing": False, "track": None}

        props = await asyncio.wait_for(session.try_get_media_properties_async(), timeout=_SMTC_TIMEOUT)
        playback = session.get_playback_info()
        timeline = session.get_timeline_properties()

        status_int = int(playback.playback_status)
        return {
            "success": True,
            "playing": status_int == 4,
            "track": {
                "title": props.title,
                "artist": props.artist,
                "album": props.album_title,
                "track_number": props.track_number,
                "status": _PLAYBACK_STATUS.get(status_int, "unknown"),
                "position_seconds": round(timeline.position.total_seconds()),
                "duration_seconds": round(timeline.end_time.total_seconds()),
                "shuffle": playback.is_shuffle_active,
                "repeat": _REPEAT_VALUES.get(int(playback.auto_repeat_mode), "unknown"),
                "source_app": session.source_app_user_model_id,
            },
        }
    except asyncio.TimeoutError:
        return {"success": False, "error": "SMTC request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── SMTC playback control tools ───────────────────────────────────────────────

@mcp.tool()
async def smtc_play() -> Dict[str, Any]:
    """
    Resume playback of the current media session via Windows SMTC.

    Returns:
        Dict with success flag. False if no session is active or the app rejected the command.
    """
    if not SMTC_AVAILABLE:
        return _SMTC_NOT_AVAILABLE
    try:
        session = await _smtc_session()
        if not session:
            return {"success": False, "error": "No active media session"}
        ok = await asyncio.wait_for(session.try_play_async(), timeout=_SMTC_TIMEOUT)
        return {"success": bool(ok)}
    except asyncio.TimeoutError:
        return {"success": False, "error": "SMTC request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def smtc_pause() -> Dict[str, Any]:
    """
    Pause playback of the current media session via Windows SMTC.

    Returns:
        Dict with success flag.
    """
    if not SMTC_AVAILABLE:
        return _SMTC_NOT_AVAILABLE
    try:
        session = await _smtc_session()
        if not session:
            return {"success": False, "error": "No active media session"}
        ok = await asyncio.wait_for(session.try_pause_async(), timeout=_SMTC_TIMEOUT)
        return {"success": bool(ok)}
    except asyncio.TimeoutError:
        return {"success": False, "error": "SMTC request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def smtc_play_pause() -> Dict[str, Any]:
    """
    Toggle play/pause for the current media session via Windows SMTC.

    Returns:
        Dict with success flag and the new playback state.
    """
    if not SMTC_AVAILABLE:
        return _SMTC_NOT_AVAILABLE
    try:
        session = await _smtc_session()
        if not session:
            return {"success": False, "error": "No active media session"}
        playback = session.get_playback_info()
        is_playing = int(playback.playback_status) == 4
        if is_playing:
            ok = await asyncio.wait_for(session.try_pause_async(), timeout=_SMTC_TIMEOUT)
            new_state = "paused"
        else:
            ok = await asyncio.wait_for(session.try_play_async(), timeout=_SMTC_TIMEOUT)
            new_state = "playing"
        return {"success": bool(ok), "state": new_state}
    except asyncio.TimeoutError:
        return {"success": False, "error": "SMTC request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def smtc_next_track() -> Dict[str, Any]:
    """
    Skip to the next track in the current media session via Windows SMTC.

    Returns:
        Dict with success flag.
    """
    if not SMTC_AVAILABLE:
        return _SMTC_NOT_AVAILABLE
    try:
        session = await _smtc_session()
        if not session:
            return {"success": False, "error": "No active media session"}
        ok = await asyncio.wait_for(session.try_skip_next_async(), timeout=_SMTC_TIMEOUT)
        return {"success": bool(ok)}
    except asyncio.TimeoutError:
        return {"success": False, "error": "SMTC request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def smtc_previous_track() -> Dict[str, Any]:
    """
    Go to the previous track in the current media session via Windows SMTC.

    Returns:
        Dict with success flag.
    """
    if not SMTC_AVAILABLE:
        return _SMTC_NOT_AVAILABLE
    try:
        session = await _smtc_session()
        if not session:
            return {"success": False, "error": "No active media session"}
        ok = await asyncio.wait_for(session.try_skip_previous_async(), timeout=_SMTC_TIMEOUT)
        return {"success": bool(ok)}
    except asyncio.TimeoutError:
        return {"success": False, "error": "SMTC request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def smtc_seek(position_seconds: float) -> Dict[str, Any]:
    """
    Seek to a specific position in the currently playing track via Windows SMTC.

    Args:
        position_seconds: Target position in seconds from the start of the track.

    Returns:
        Dict with success flag.
    """
    if not SMTC_AVAILABLE:
        return _SMTC_NOT_AVAILABLE
    try:
        session = await _smtc_session()
        if not session:
            return {"success": False, "error": "No active media session"}
        # WinRT TimeSpan is in 100-nanosecond ticks
        ticks = int(position_seconds * 10_000_000)
        ok = await asyncio.wait_for(session.try_change_playback_position_async(ticks), timeout=_SMTC_TIMEOUT)
        return {"success": bool(ok), "position_seconds": position_seconds}
    except asyncio.TimeoutError:
        return {"success": False, "error": "SMTC request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def smtc_set_shuffle(active: bool) -> Dict[str, Any]:
    """
    Enable or disable shuffle for the current media session via Windows SMTC.

    Args:
        active: True to enable shuffle, False to disable.

    Returns:
        Dict with success flag.
    """
    if not SMTC_AVAILABLE:
        return _SMTC_NOT_AVAILABLE
    try:
        session = await _smtc_session()
        if not session:
            return {"success": False, "error": "No active media session"}
        ok = await asyncio.wait_for(session.try_change_shuffle_active_async(active), timeout=_SMTC_TIMEOUT)
        return {"success": bool(ok), "shuffle": active}
    except asyncio.TimeoutError:
        return {"success": False, "error": "SMTC request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def smtc_set_repeat(mode: str) -> Dict[str, Any]:
    """
    Set the repeat mode for the current media session via Windows SMTC.

    Args:
        mode: One of "none" (no repeat), "track" (repeat current track),
              or "list" (repeat the playlist/album).

    Returns:
        Dict with success flag.
    """
    if not SMTC_AVAILABLE:
        return _SMTC_NOT_AVAILABLE
    mode = mode.lower()
    if mode not in _REPEAT_NAMES:
        return {"success": False, "error": f"mode must be one of: {list(_REPEAT_NAMES)}"}
    try:
        session = await _smtc_session()
        if not session:
            return {"success": False, "error": "No active media session"}
        repeat_value = _RepeatMode(int(_REPEAT_NAMES[mode]))
        ok = await asyncio.wait_for(session.try_change_auto_repeat_mode_async(repeat_value), timeout=_SMTC_TIMEOUT)
        return {"success": bool(ok), "repeat": mode}
    except asyncio.TimeoutError:
        return {"success": False, "error": "SMTC request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# MCP Resources
@mcp.resource("deezer://api-endpoints")
async def get_api_endpoints() -> str:
    """Documentation of available Deezer API endpoints."""
    endpoints = {
        "search": {
            "tracks": "/search?q={query}",
            "artists": "/search/artist?q={query}",
            "albums": "/search/album?q={query}",
            "playlists": "/search/playlist?q={query}",
        },
        "details": {
            "track": "/track/{id}",
            "artist": "/artist/{id}",
            "album": "/album/{id}",
            "playlist": "/playlist/{id}",
        },
        "artist_content": {
            "albums": "/artist/{id}/albums",
            "top_tracks": "/artist/{id}/top",
            "related": "/artist/{id}/related",
        },
        "album_content": {
            "tracks": "/album/{id}/tracks",
        },
        "charts": "/chart/{genre_id}",
        "genres": "/genre",
    }
    return json.dumps(endpoints, indent=2)


@mcp.resource("deezer://search-examples")
async def get_search_examples() -> str:
    """Examples of basic and advanced Deezer searches."""
    examples = {
        "basic_search": {
            "description": "Simple track search",
            "example": 'search_tracks({"query": "daft punk", "limit": 10})',
        },
        "advanced_search": {
            "description": "Search with specific criteria",
            "examples": [
                'advanced_search({"artist": "daft punk", "bpm_min": 120})',
                'advanced_search({"album": "random access memories", "dur_min": 300})',
                'advanced_search({"track": "get lucky", "label": "columbia"})',
            ],
        },
        "search_modifiers": {
            "artist": 'artist:"artist name"',
            "album": 'album:"album title"',
            "track": 'track:"track title"',
            "label": 'label:"label name"',
            "duration": "dur_min:300 dur_max:500",
            "bpm": "bpm_min:120 bpm_max:140",
        },
    }
    return json.dumps(examples, indent=2)


@mcp.prompt("deezer-search-assistant")
async def deezer_search_assistant() -> str:
    """System prompt for Deezer + Last.fm music assistance."""
    return """
You are a music assistant with access to both the Deezer catalog and the user's personal Last.fm listening data.

## Deezer — catalog search and browsing
- search_tracks, advanced_search — find tracks (supports artist, BPM, duration, label filters)
- search_artists, search_albums, search_playlists — find content by name
- get_track_details, get_artist_details, get_album_details, get_playlist_details — look up by ID
- get_artist_albums, get_artist_top_tracks, get_artist_related, get_album_tracks — explore discographies
- get_chart — current trending tracks/albums/artists/playlists (filter by genre)
- get_genre_list, get_genre_artists — browse by genre

## Last.fm — personal listening data (read)
- lastfm_get_now_playing — what the user is listening to right now
- lastfm_get_recent_tracks — recent listening history
- lastfm_get_top_tracks, lastfm_get_top_artists — most played over a period (7day/1month/3month/6month/12month/overall)
- lastfm_get_loved_tracks — tracks the user has hearted
- lastfm_get_similar_artists — artists similar to a given one (no auth needed)
- lastfm_get_track_info — tags, wiki, listener counts, user play count

## Last.fm — write actions (require session key)
- lastfm_love_track / lastfm_unlove_track — heart or unheart a track
- lastfm_update_now_playing — push a now-playing status manually
- lastfm_scrobble — submit a listen record

## Windows SMTC — real-time playback control (Windows only)
- smtc_get_now_playing — current track from any media app (Deezer, Spotify, browser, etc.)
- smtc_play / smtc_pause / smtc_play_pause — playback control
- smtc_next_track / smtc_previous_track — track navigation
- smtc_seek — jump to a position (in seconds)
- smtc_set_shuffle — enable/disable shuffle
- smtc_set_repeat — set repeat mode (none / track / list)

## Workflow tips
- Use smtc_get_now_playing for real-time track info, lastfm_get_now_playing for scrobble-based history
- smtc_get_now_playing → lastfm_get_similar_artists → search_tracks is a full discovery loop from current context
- Use lastfm_get_top_artists to understand taste, then search Deezer for new releases by those artists
- Combine lastfm_get_loved_tracks with advanced_search to find similar tracks by BPM or duration
- Write operations (love, scrobble) require LASTFM_SESSION_KEY — tell the user to run lastfm_auth.py if not set
- SMTC tools return success: false with a clear message if not on Windows or winrt not installed
"""


def main():
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    transport = os.environ.get("MCP_TRANSPORT", "http")

    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport in ("http", "streamable-http", "sse"):
        # stateless_http avoids the session initialization handshake that breaks
        # some clients (including AnythingLLM) which send requests before the
        # MCP initialize round-trip completes on a persistent SSE session.
        mcp.run(transport=transport, host=host, port=port, stateless_http=True)
    else:
        raise ValueError(f"Unsupported transport: {transport}")


if __name__ == "__main__":
    main()
