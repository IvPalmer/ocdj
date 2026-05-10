"""
YouTube collector for finding album/track listening links
Enhances crate-mate with YouTube support since many vinyl releases aren't on Spotify
"""
import os
import logging
from typing import Optional, Dict, List
import requests
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from .base import MetadataCollector

load_dotenv()

# Falls back to ocdj's existing YOUTUBE_API_KEY since both modules ultimately
# call the same Google quota — no need for a cratemate-specific key unless the
# operator wants per-module billing visibility.
YOUTUBE_API_KEY = os.getenv('CRATEMATE_YOUTUBE_API_KEY') or os.getenv('YOUTUBE_API_KEY')
YOUTUBE_BASE_URL = 'https://www.googleapis.com/youtube/v3'


class YouTubeCollector(MetadataCollector):
    """
    Fetch listening links from YouTube
    """
    
    def __init__(self, name: str):
        super().__init__(name)
        self.logger.info("Initializing YouTubeCollector")
        self.api_key = YOUTUBE_API_KEY
        self.executor = ThreadPoolExecutor(max_workers=5)
        
        if not self.api_key:
            self.logger.warning("YouTube API key not configured")
    
    async def fetch_artist_details(self, artist_name: str) -> dict:
        """
        Fetch artist details from YouTube
        Since YouTube isn't primarily for artist metadata, we return minimal info
        """
        if not self.api_key:
            return {"error": "YouTube API key not configured"}
        
        # YouTube doesn't provide artist metadata like other services
        # We'll just search for the artist's channel
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                self._search_artist_channel,
                artist_name
            )
            
            if result:
                return {
                    "name": artist_name,
                    "url": result['url'],
                    "image": result.get('thumbnail'),
                    "profile": f"YouTube channel: {result['channel']}",
                    "youtube_channel": result['channel'],
                    "youtube_channel_url": result['url']
                }
            
            return {
                "name": artist_name,
                "profile": "No YouTube channel found"
            }
            
        except Exception as e:
            self.logger.error(f"Error fetching artist details: {str(e)}")
            return {"error": str(e)}
    
    async def fetch_album_details(self, artist_name: str, album_name: str) -> dict:
        """
        Fetch album details from YouTube
        Focus on finding listening links
        """
        if not self.api_key:
            return {"error": "YouTube API key not configured"}
        
        try:
            loop = asyncio.get_event_loop()
            
            # Search for full album
            album_result = await loop.run_in_executor(
                self.executor,
                self._search_album,
                artist_name,
                album_name
            )
            
            # Search for individual tracks
            track_results = await loop.run_in_executor(
                self.executor,
                self._search_tracks,
                artist_name,
                album_name
            )
            
            album_details = {
                "name": album_name,
                "artist": artist_name,
            }
            
            # Add YouTube specific fields
            if album_result:
                album_details["youtube_url"] = album_result['url']
                album_details["youtube_title"] = album_result['title']
                album_details["youtube_channel"] = album_result['channel']
                album_details["youtube_thumbnail"] = album_result['thumbnail']
                album_details["youtube_confidence"] = album_result.get('confidence', 0.5)
                album_details["url"] = album_result['url']  # Primary listening link
                album_details["image"] = album_result['thumbnail']
            
            if track_results:
                album_details["youtube_tracks"] = track_results
                album_details["tracks"] = [
                    {
                        "name": track['track'],
                        "url": track['url'],
                        "duration": None  # Would need additional API call
                    }
                    for track in track_results
                ]
            
            return album_details
            
        except Exception as e:
            self.logger.error(f"Error fetching album details: {str(e)}")
            return {"error": str(e)}
    
    def _search_artist_channel(self, artist_name: str) -> Optional[Dict]:
        """Search for artist's official channel"""
        params = {
            'part': 'snippet',
            'q': artist_name,
            'type': 'channel',
            'maxResults': 5,
            'key': self.api_key
        }
        
        try:
            response = requests.get(f"{YOUTUBE_BASE_URL}/search", params=params)
            if response.status_code == 200:
                data = response.json()
                items = data.get('items', [])
                
                # Look for official/verified channels
                for item in items:
                    snippet = item['snippet']
                    channel_title = snippet['channelTitle']
                    
                    # Check if it might be official
                    if (artist_name.lower() in channel_title.lower() or
                        'official' in channel_title.lower() or
                        'vevo' in channel_title.lower()):
                        
                        return {
                            'channel': channel_title,
                            'url': f"https://www.youtube.com/channel/{item['snippet']['channelId']}",
                            'thumbnail': snippet['thumbnails']['high']['url']
                        }
                
                # Return first result if no official found
                if items:
                    item = items[0]
                    return {
                        'channel': item['snippet']['channelTitle'],
                        'url': f"https://www.youtube.com/channel/{item['snippet']['channelId']}",
                        'thumbnail': item['snippet']['thumbnails']['high']['url']
                    }
        
        except Exception as e:
            self.logger.error(f"Error searching artist channel: {str(e)}")
        
        return None
    
    def _search_album(self, artist: str, album: str) -> Optional[Dict]:
        """Search for full album on YouTube"""
        search_queries = [
            f"{artist} {album} full album",
            f"{artist} {album} album",
            f"{artist} {album} vinyl"
        ]
        
        for query in search_queries:
            result = self._search_youtube(query, prefer_long_videos=True)
            if result:
                # Calculate confidence
                confidence = self._calculate_confidence(
                    result['title'],
                    artist,
                    album
                )
                result['confidence'] = confidence
                
                # Return if high confidence
                if confidence > 0.7:
                    return result
        
        # Return best result if any
        return result if 'result' in locals() else None
    
    def _search_tracks(self, artist: str, album: str) -> List[Dict]:
        """Search for individual tracks (placeholder for now)"""
        # This would require first getting track list from another source
        # For now, return empty list
        return []
    
    def _search_youtube(self, query: str, prefer_long_videos: bool = True) -> Optional[Dict]:
        """Perform YouTube search and return best result"""
        params = {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'maxResults': 10,
            'key': self.api_key
        }
        
        if prefer_long_videos:
            params['videoDuration'] = 'long'  # >20 minutes
        
        try:
            response = requests.get(f"{YOUTUBE_BASE_URL}/search", params=params)
            
            if response.status_code == 200:
                data = response.json()
                items = data.get('items', [])
                
                if items:
                    # Find best match
                    best_item = self._select_best_video(items)
                    if best_item:
                        return {
                            'url': f"https://www.youtube.com/watch?v={best_item['id']['videoId']}",
                            'title': best_item['snippet']['title'],
                            'channel': best_item['snippet']['channelTitle'],
                            'thumbnail': best_item['snippet']['thumbnails']['high']['url'],
                            'description': best_item['snippet']['description'][:200]
                        }
        
        except Exception as e:
            self.logger.error(f"Error searching YouTube: {str(e)}")
        
        return None
    
    def _select_best_video(self, items: List[Dict]) -> Optional[Dict]:
        """Select best video from search results"""
        # Prioritize official/topic channels
        priority_keywords = ['official', 'vevo', 'topic', 'records', 'label']
        
        for item in items:
            channel = item['snippet']['channelTitle'].lower()
            if any(keyword in channel for keyword in priority_keywords):
                return item
        
        # Return first result if no priority match
        return items[0] if items else None
    
    def _calculate_confidence(self, video_title: str, artist: str, album: str) -> float:
        """Calculate confidence score for a match"""
        video_title_lower = video_title.lower()
        artist_lower = artist.lower()
        album_lower = album.lower()
        
        score = 0.0
        
        # Check artist match
        if artist_lower in video_title_lower:
            score += 0.4
        
        # Check album match
        if album_lower in video_title_lower:
            score += 0.4
        
        # Bonus for "full album" in title
        if 'full album' in video_title_lower:
            score += 0.2
        
        # Penalty for "live" unless album name contains "live"
        if 'live' in video_title_lower and 'live' not in album_lower:
            score -= 0.3
        
        return max(0.0, min(1.0, score))