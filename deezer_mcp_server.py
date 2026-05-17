"""
Deezer MCP Server
Provides search, retrieval, and exploration of music content via the Deezer API.
"""

import asyncio
import logging
import os
from typing import Dict, Any, Optional
import aiohttp
from fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Deezer Music Server")

BASE_URL = "https://api.deezer.com"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

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
    """System prompt for Deezer music search assistance."""
    return """
You are a music discovery assistant with access to the Deezer music catalog via MCP tools.

You can help users with:

1. **Track Search**: Find specific songs using search_tracks or advanced_search with criteria like artist, BPM, duration, and label.

2. **Artist Exploration**: Look up artists with search_artists, get their details with get_artist_details, browse their discography with get_artist_albums, find their hits with get_artist_top_tracks, or discover similar artists with get_artist_related.

3. **Album Exploration**: Find albums with search_albums, get full details with get_album_details, or list all tracks with get_album_tracks.

4. **Playlists**: Search with search_playlists or get details with get_playlist_details.

5. **Charts**: Get current trending music with get_chart (optionally filtered by genre).

6. **Genres**: Browse all genres with get_genre_list, then find genre artists with get_genre_artists.

Tips:
- Use strict=true for exact matches, strict=false (default) for fuzzy matching
- Use advanced_search for precise filtering: artist:"name" track:"title" bpm_min:120
- Chain tools: search for an artist → get their ID → get their top tracks or albums
- For music discovery: get_chart for trending, get_genre_artists for genre exploration
"""


def main():
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    transport = os.environ.get("MCP_TRANSPORT", "sse")

    if transport == "stdio":
        mcp.run()
    elif transport == "sse":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        raise ValueError(f"Unsupported transport: {transport}")


if __name__ == "__main__":
    main()
