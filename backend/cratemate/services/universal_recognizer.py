"""
Universal Vinyl Record Recognizer
Identifies ANY vinyl record without relying on a pre-loaded database
"""
import os
import logging
import base64
from typing import Dict, List, Optional, Tuple
from io import BytesIO
import asyncio
import aiohttp
from PIL import Image
import requests
from dotenv import load_dotenv

# Import existing modules
from .discogs import DiscogsCollector
from .spotify import SpotifyCollector
from .youtube import YouTubeCollector
from .musicbrainz import MusicBrainzCollector


# crate-mate's `app.process.cover_extractor` was a heavy CV pipeline that
# preprocessed photos to crop to the cover. V1 expects callers to pass a
# pre-cropped image (frontend handles aspect-ratio guidance). If real-world
# usage shows tilted/cluttered photos miss, port the extractor in V2.
def extract_album_cover(image_file):  # noqa: D401 — passthrough
    """V1 passthrough — return image as-is (caller pre-crops)."""
    from PIL import Image
    if hasattr(image_file, 'read'):
        return Image.open(image_file)
    return image_file

load_dotenv()

logger = logging.getLogger(__name__)

# Google Vision API setup (optional but recommended)
GOOGLE_VISION_API_KEY = os.getenv('GOOGLE_VISION_API_KEY')
GOOGLE_VISION_URL = 'https://vision.googleapis.com/v1/images:annotate'


class UniversalVinylRecognizer:
    """
    Recognizes vinyl records using multiple methods without database dependency
    """
    
    def __init__(self):
        self.logger = logger
        self.logger.info("Initializing Universal Vinyl Recognizer")
        
        # Initialize collectors
        self.discogs = DiscogsCollector("discogs")
        self.spotify = SpotifyCollector("spotify")
        self.youtube = YouTubeCollector("youtube")
        self.musicbrainz = MusicBrainzCollector("musicbrainz")
        
        # Google Vision API available?
        self.vision_available = bool(GOOGLE_VISION_API_KEY)
        if self.vision_available:
            self.logger.info("Google Vision API configured")
        else:
            self.logger.warning("Google Vision API not configured - using fallback methods")
    
    async def identify_album(self, image_file) -> Dict:
        """
        Main entry point for album identification
        """
        try:
            # Step 1: Extract album cover from photo
            self.logger.info("Extracting album cover from image")
            album_cover = await self._extract_cover(image_file)
            
            # Step 2: Extract information using multiple methods
            self.logger.info("Extracting information from album cover")
            extracted_info = await self._extract_information(album_cover)
            
            # Step 3: Build smart search queries
            self.logger.info("Building search queries")
            search_queries = self._build_search_queries(extracted_info)
            
            # Step 4: Search multiple sources
            self.logger.info(f"Searching with {len(search_queries)} queries")
            search_results = await self._search_all_sources(search_queries)
            
            # Step 5: Rank and select best match
            self.logger.info("Ranking results")
            best_match = self._rank_results(search_results, extracted_info)
            
            if best_match:
                # Step 6: Enrich with metadata from all sources
                self.logger.info("Enriching metadata")
                enriched_result = await self._enrich_metadata(best_match)
                return {
                    "success": True,
                    "album": enriched_result,
                    "confidence": best_match.get("confidence", 0.5),
                    "recognition_method": best_match.get("method", "unknown")
                }
            else:
                return {
                    "success": False,
                    "error": "Could not identify album",
                    "extracted_info": extracted_info,
                    "queries_tried": search_queries
                }
        
        except Exception as e:
            self.logger.error(f"Error in album identification: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _extract_cover(self, image_file) -> Image.Image:
        """Extract album cover from uploaded image"""
        # Use existing cover extractor
        cover = extract_album_cover(image_file)
        return cover
    
    async def _extract_information(self, album_cover: Image.Image) -> Dict:
        """
        Extract information using multiple methods
        """
        info = {
            "text": [],
            "logos": [],
            "labels": [],
            "colors": [],
            "web_entities": [],
            "visual_matches": []
        }
        
        # Method 1: Google Vision API (if available)
        if self.vision_available:
            vision_results = await self._google_vision_analyze(album_cover)
            info.update(vision_results)
        
        # Method 2: Basic image analysis (always available)
        info["colors"] = self._extract_dominant_colors(album_cover)
        info["image_hash"] = self._generate_image_hash(album_cover)
        
        # Method 3: OCR fallback (if no Google Vision)
        if not self.vision_available:
            # Could use pytesseract or other OCR here
            pass
        
        return info
    
    async def _google_vision_analyze(self, image: Image.Image) -> Dict:
        """Use Google Vision API to analyze image"""
        try:
            # Convert image to base64
            buffered = BytesIO()
            image.save(buffered, format="JPEG")
            image_base64 = base64.b64encode(buffered.getvalue()).decode()
            
            # Prepare request
            request_data = {
                "requests": [{
                    "image": {
                        "content": image_base64
                    },
                    "features": [
                        {"type": "TEXT_DETECTION", "maxResults": 10},
                        {"type": "LOGO_DETECTION", "maxResults": 5},
                        {"type": "LABEL_DETECTION", "maxResults": 10},
                        {"type": "WEB_DETECTION", "maxResults": 10}
                    ]
                }]
            }
            
            # Make request
            headers = {"Content-Type": "application/json"}
            params = {"key": GOOGLE_VISION_API_KEY}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    GOOGLE_VISION_URL,
                    json=request_data,
                    headers=headers,
                    params=params
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return self._parse_vision_response(data)
                    else:
                        self.logger.error(f"Google Vision API error: {response.status}")
                        return {}
        
        except Exception as e:
            self.logger.error(f"Error calling Google Vision API: {str(e)}")
            return {}
    
    def _parse_vision_response(self, response: Dict) -> Dict:
        """Parse Google Vision API response"""
        info = {
            "text": [],
            "logos": [],
            "labels": [],
            "web_entities": [],
            "visual_matches": []
        }
        
        if "responses" in response and response["responses"]:
            result = response["responses"][0]
            
            # Extract text
            if "textAnnotations" in result:
                for text in result["textAnnotations"][1:]:  # Skip first (full text)
                    info["text"].append(text["description"])
            
            # Extract logos
            if "logoAnnotations" in result:
                for logo in result["logoAnnotations"]:
                    info["logos"].append(logo["description"])
            
            # Extract labels
            if "labelAnnotations" in result:
                for label in result["labelAnnotations"]:
                    info["labels"].append(label["description"])
            
            # Extract web entities
            if "webDetection" in result:
                web = result["webDetection"]
                if "webEntities" in web:
                    for entity in web["webEntities"]:
                        if "description" in entity:
                            info["web_entities"].append(entity["description"])
                
                # Pages with matching images
                if "pagesWithMatchingImages" in web:
                    for page in web["pagesWithMatchingImages"][:5]:
                        info["visual_matches"].append({
                            "url": page.get("url", ""),
                            "title": page.get("pageTitle", "")
                        })
        
        return info
    
    def _extract_dominant_colors(self, image: Image.Image) -> List[str]:
        """Extract dominant colors from image"""
        # Simple color extraction
        img = image.resize((50, 50))  # Resize for faster processing
        pixels = img.getdata()
        
        # Get most common colors
        from collections import Counter
        color_counts = Counter(pixels)
        
        # Return top 5 colors as hex
        dominant_colors = []
        for color, count in color_counts.most_common(5):
            if len(color) >= 3:  # RGB
                hex_color = '#{:02x}{:02x}{:02x}'.format(color[0], color[1], color[2])
                dominant_colors.append(hex_color)
        
        return dominant_colors
    
    def _generate_image_hash(self, image: Image.Image) -> str:
        """Generate a perceptual hash of the image"""
        # Simple average hash implementation
        img = image.convert('L').resize((8, 8))
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        
        hash_bits = ''.join(['1' if pixel > avg else '0' for pixel in pixels])
        return hex(int(hash_bits, 2))[2:]
    
    def _build_search_queries(self, extracted_info: Dict) -> List[Dict]:
        """Build smart search queries from extracted information"""
        queries = []
        
        # Text-based queries
        text_elements = extracted_info.get("text", [])
        if len(text_elements) >= 2:
            # Assume artist - album format
            queries.append({
                "type": "artist_album",
                "query": f"{text_elements[0]} {text_elements[1]}",
                "confidence": 0.8
            })
        
        # Single text elements
        for text in text_elements[:5]:
            if len(text) > 2:  # Skip very short text
                queries.append({
                    "type": "text",
                    "query": text,
                    "confidence": 0.6
                })
        
        # Logo-based queries (record labels)
        for logo in extracted_info.get("logos", []):
            queries.append({
                "type": "label",
                "query": logo,
                "confidence": 0.7
            })
        
        # Web entity queries
        for entity in extracted_info.get("web_entities", [])[:3]:
            queries.append({
                "type": "web_entity",
                "query": entity,
                "confidence": 0.7
            })
        
        # Catalog number patterns
        import re
        for text in text_elements:
            if re.match(r'^[A-Z]{2,5}[-\s]?\d{2,5}$', text):
                queries.append({
                    "type": "catalog",
                    "query": text,
                    "confidence": 0.9
                })
        
        return queries
    
    async def _search_all_sources(self, queries: List[Dict]) -> List[Dict]:
        """Search multiple sources with our queries"""
        all_results = []
        
        for query_info in queries:
            query = query_info["query"]
            query_type = query_info["type"]
            
            # Search Discogs
            try:
                if query_type == "catalog":
                    # Special handling for catalog numbers
                    results = await self._search_discogs_catalog(query)
                else:
                    results = await self._search_discogs_general(query)
                
                for result in results[:3]:  # Limit results per query
                    result["source"] = "discogs"
                    result["query_confidence"] = query_info["confidence"]
                    result["query_type"] = query_type
                    all_results.append(result)
            
            except Exception as e:
                self.logger.error(f"Error searching Discogs: {str(e)}")
        
        return all_results
    
    async def _search_discogs_general(self, query: str) -> List[Dict]:
        """Search Discogs with a general query"""
        # This would use the Discogs API
        # For now, return empty list
        return []
    
    async def _search_discogs_catalog(self, catalog: str) -> List[Dict]:
        """Search Discogs by catalog number"""
        # This would use the Discogs API with catalog search
        # For now, return empty list
        return []
    
    def _rank_results(self, results: List[Dict], extracted_info: Dict) -> Optional[Dict]:
        """Rank results and return the best match"""
        if not results:
            return None
        
        # Score each result
        for result in results:
            score = 0.0
            
            # Base score from query confidence
            score += result.get("query_confidence", 0.5) * 0.5
            
            # Boost for catalog number matches
            if result.get("query_type") == "catalog":
                score += 0.3
            
            # Boost for multiple matching fields
            # (would implement more sophisticated matching here)
            
            result["final_score"] = score
        
        # Sort by score
        results.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        
        # Return best match if score is high enough
        best = results[0]
        if best.get("final_score", 0) > 0.3:
            best["confidence"] = best["final_score"]
            return best
        
        return None
    
    async def _enrich_metadata(self, match: Dict) -> Dict:
        """Enrich the match with metadata from multiple sources"""
        enriched = match.copy()
        
        # Get Spotify data if available
        try:
            artist = match.get("artist", "")
            album = match.get("title", "")
            if artist and album:
                spotify_data = await self.spotify.fetch_album_details(artist, album)
                if spotify_data and "url" in spotify_data:
                    enriched["spotify_url"] = spotify_data["url"]
        except Exception as e:
            self.logger.error(f"Error fetching Spotify data: {str(e)}")
        
        # Get YouTube link
        try:
            youtube_data = await self.youtube.fetch_album_details(artist, album)
            if youtube_data and "youtube_url" in youtube_data:
                enriched["youtube_url"] = youtube_data["youtube_url"]
        except Exception as e:
            self.logger.error(f"Error fetching YouTube data: {str(e)}")
        
        return enriched
