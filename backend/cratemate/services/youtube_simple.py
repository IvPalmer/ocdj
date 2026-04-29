"""
Simple YouTube search without API limits
Builds search URLs directly
"""
import logging
from typing import Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


class SimpleYouTubeSearch:
    """
    Generate YouTube search URLs without API
    """
    
    def __init__(self):
        self.base_search_url = "https://www.youtube.com/results?search_query="
        logger.info("Simple YouTube search initialized (no API needed)")
    
    def build_search_url(self, query: str) -> str:
        """Build a YouTube search URL for the given query"""
        encoded_query = quote_plus(query)
        return f"{self.base_search_url}{encoded_query}"
    
    def generate_track_searches(self, artist: str, album: str, tracklist: List[Dict]) -> List[Dict]:
        """
        Generate YouTube search links for each track
        Returns formatted track data with search URLs
        """
        enhanced_tracks = []
        
        for track in tracklist:
            track_title = track.get("title", "")
            position = track.get("position", "")
            
            if track_title:
                # Build search queries with different strategies
                search_queries = [
                    f"{artist} {track_title}",  # Most common format
                    f"{artist} - {track_title}",
                    f"{artist} {track_title} {album}",
                    f"{artist} {track_title} official"
                ]
                
                enhanced_track = {
                    "position": position,
                    "title": track_title,
                    "duration": track.get("duration", ""),
                    "artist": artist,
                    "album": album,
                    "youtube_search_urls": [
                        {
                            "query": query,
                            "url": self.build_search_url(query)
                        }
                        for query in search_queries
                    ],
                    "primary_search_url": self.build_search_url(search_queries[0]),
                    "youtube_embed_search": f"https://www.youtube.com/embed/results?search_query={quote_plus(search_queries[0])}"
                }
                
                enhanced_tracks.append(enhanced_track)
        
        return enhanced_tracks
    
    def generate_album_search(self, artist: str, album: str) -> Dict:
        """Generate search URLs for the full album"""
        queries = [
            f"{artist} {album} full album",
            f"{artist} {album} album",
            f"{artist} - {album}",
            f"{artist} {album} playlist"
        ]
        
        return {
            "album_searches": [
                {
                    "query": query,
                    "url": self.build_search_url(query)
                }
                for query in queries
            ],
            "primary_album_url": self.build_search_url(queries[0])
        }

