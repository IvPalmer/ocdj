"""
Gemini Vision API collector for album identification
"""
import os
import logging
from typing import Dict, Optional
import google.generativeai as genai
from PIL import Image

logger = logging.getLogger(__name__)


class GeminiCollector:
    """
    Uses Google's Gemini Vision API to identify album covers
    """

    def __init__(self):
        """Initialize Gemini with API key from environment.

        V1 ocdj behaviour: if the key is missing or still the __PENDING__
        placeholder, mark the collector as unconfigured rather than crashing
        at import time. The view layer surfaces a clean 503 instead.
        """
        api_key = os.getenv('CRATEMATE_GEMINI_API_KEY') or os.getenv('GEMINI_API_KEY')
        self.configured = bool(api_key) and api_key != '__PENDING__'
        if not self.configured:
            self.model = None
            logger.warning(
                "Gemini Vision collector not configured "
                "(CRATEMATE_GEMINI_API_KEY missing or placeholder); "
                "identify endpoint will return 503."
            )
            return

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        logger.info("Gemini Vision collector initialized")
    
    async def identify_album(self, image: Image.Image) -> Dict:
        """
        Use Gemini to identify an album from its cover
        
        Returns:
            Dict with identified album info or error
        """
        try:
            # Create a prompt that will help Gemini identify the album
            prompt = """
            Please identify this album cover. Analyze the image carefully and provide:
            
            1. Artist name (exactly as shown or as you recognize it)
            2. Album title (exactly as shown or as you recognize it)
            3. Any visible text on the cover (transcribe exactly)
            4. Genre (if you can identify it)
            5. Approximate year/era (if you can tell)
            6. Visual description (colors, style, notable features)
            7. Confidence level (high/medium/low)
            
            If you recognize this as a specific album, please be precise with the artist and album names.
            If there's text that's partially obscured or stylized, do your best to read it.
            
            Format your response as:
            ARTIST: [artist name]
            ALBUM: [album title]
            VISIBLE_TEXT: [any text you can see]
            GENRE: [genre]
            ERA: [year or decade]
            DESCRIPTION: [visual description]
            CONFIDENCE: [high/medium/low]
            
            If you cannot determine something, write "unknown" for that field.
            """
            
            # Generate response
            response = self.model.generate_content([prompt, image])
            
            if response.text:
                # Parse the response
                result = self._parse_gemini_response(response.text)
                logger.info(f"Gemini identified: {result.get('artist', 'Unknown')} - {result.get('album', 'Unknown')}")
                return {
                    "success": True,
                    "result": result,
                    "raw_response": response.text
                }
            else:
                logger.error("Gemini returned empty response")
                return {
                    "success": False,
                    "error": "Empty response from Gemini"
                }
                
        except Exception as e:
            logger.error(f"Gemini Vision error: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _parse_gemini_response(self, text: str) -> Dict:
        """Parse Gemini's structured response"""
        result = {
            "artist": "unknown",
            "album": "unknown",
            "visible_text": "",
            "genre": "unknown",
            "era": "unknown",
            "description": "",
            "confidence": "low"
        }
        
        lines = text.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('ARTIST:'):
                result["artist"] = line.replace('ARTIST:', '').strip()
            elif line.startswith('ALBUM:'):
                result["album"] = line.replace('ALBUM:', '').strip()
            elif line.startswith('VISIBLE_TEXT:'):
                result["visible_text"] = line.replace('VISIBLE_TEXT:', '').strip()
            elif line.startswith('GENRE:'):
                result["genre"] = line.replace('GENRE:', '').strip()
            elif line.startswith('ERA:'):
                result["era"] = line.replace('ERA:', '').strip()
            elif line.startswith('DESCRIPTION:'):
                result["description"] = line.replace('DESCRIPTION:', '').strip()
            elif line.startswith('CONFIDENCE:'):
                result["confidence"] = line.replace('CONFIDENCE:', '').strip().lower()
        
        # Clean up "unknown" values
        for key in result:
            if result[key].lower() in ["unknown", "n/a", "not available", ""]:
                result[key] = None if key != "confidence" else "low"
        
        return result
    
    async def find_youtube_links(self, artist: str, album: str, tracks: list) -> Dict:
        """
        Use Gemini to find YouTube video links for tracks
        
        Returns:
            Dict with YouTube links for each track
        """
        try:
            # Create a prompt asking Gemini to find YouTube videos
            prompt = f"""
            I need to find YouTube video links for these tracks from the album "{album}" by {artist}.
            
            For each track, please provide the most likely YouTube video ID or URL if you know it.
            These are typically the official uploads, popular uploads, or well-known versions.
            
            Tracks:
            """
            
            for i, track in enumerate(tracks, 1):
                track_title = track.get("title", "Unknown")
                position = track.get("position", str(i))
                prompt += f"\n{position}. {track_title}"
            
            prompt += """
            
            Please respond with the YouTube video information in this exact format:
            TRACK_POSITION: VIDEO_ID or VIDEO_URL or "unknown"
            
            For example:
            A1: dQw4w9WgXcQ
            A2: https://youtube.com/watch?v=abc123
            B1: unknown
            
            If you know the video ID or can make a very confident guess based on your knowledge, provide it.
            Otherwise, write "unknown".
            
            Important: Only provide actual video IDs/URLs you're confident about, don't make up IDs.
            """
            
            # Generate response
            response = self.model.generate_content(prompt)
            
            if response.text:
                # Parse the response
                result = self._parse_youtube_response(response.text, tracks)
                logger.info(f"Gemini found YouTube links for {len([t for t in result if t.get('youtube_id')])} tracks")
                return {
                    "success": True,
                    "tracks": result
                }
            else:
                logger.error("Gemini returned empty response for YouTube search")
                return {
                    "success": False,
                    "error": "Empty response from Gemini"
                }
                
        except Exception as e:
            logger.error(f"Error getting YouTube links from Gemini: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _parse_youtube_response(self, response_text: str, original_tracks: list) -> list:
        """Parse Gemini's YouTube response"""
        result = []
        
        # Create a mapping of positions for quick lookup
        position_map = {track.get("position", str(i)): track for i, track in enumerate(original_tracks, 1)}
        
        lines = response_text.strip().split('\n')
        for line in lines:
            if ':' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    position = parts[0].strip()
                    video_info = parts[1].strip()
                    
                    if position in position_map:
                        track = position_map[position].copy()
                        
                        if video_info and video_info.lower() != "unknown":
                            # Extract video ID from various formats
                            video_id = None
                            if "youtube.com/watch?v=" in video_info:
                                video_id = video_info.split("v=")[1].split("&")[0]
                            elif "youtu.be/" in video_info:
                                video_id = video_info.split("youtu.be/")[1].split("?")[0]
                            elif len(video_info) == 11:  # Likely just the video ID
                                video_id = video_info
                            
                            if video_id:
                                track["youtube_id"] = video_id
                                track["youtube_url"] = f"https://www.youtube.com/watch?v={video_id}"
                        
                        result.append(track)
        
        # Add any tracks that weren't in the response
        for position, track in position_map.items():
            if not any(t.get("position") == position for t in result):
                result.append(track.copy())
        
        return result