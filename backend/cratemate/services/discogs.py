"""
Handles direct interaction with the Discogs API, including reverse image search.
"""
import discogs_client
import os
import logging
from .base import MetadataCollector

logger = logging.getLogger(__name__)

class DiscogsCollector(MetadataCollector):
    def __init__(self, name="discogs"):
        super().__init__(name)
        self.client = None
        # CRATEMATE_DISCOGS_TOKEN is the new prefixed env; legacy DISCOGS_TOKEN
        # works if migrating from a crate-mate VPS .env.
        token = (
            os.getenv("CRATEMATE_DISCOGS_TOKEN")
            or os.getenv("DISCOGS_TOKEN")
            or os.getenv("DISCOGS_PERSONAL_TOKEN")  # ocdj wanted/recognize fallback
        )
        if token and token != '__PENDING__':
            try:
                self.client = discogs_client.Client(
                    "CrateMate/1.0", user_token=token
                )
            except Exception as e:
                self.logger.error(f"Failed to initialize Discogs client: {e}")

    def search_release(self, query: str):
        if not self.client:
            return {"success": False, "error": "Discogs client not initialized"}
        try:
            results = self.client.search(query, type="release")
            if results and results.count > 0:
                # Convert results to a list of dicts
                releases = []
                # Use page method to get actual results
                page_results = results.page(1) if hasattr(results, 'page') else list(results)[:5]
                for result in page_results[:5]:  # Limit to 5 results
                    artist = result.artists[0].name if result.artists else "Unknown Artist"
                    
                    # Get image URLs - different attributes for different result types
                    cover_image = ""
                    thumb = ""
                    if hasattr(result, 'images') and result.images:
                        cover_image = result.images[0]['uri'] if result.images[0] else ""
                    elif hasattr(result, 'cover_image'):
                        cover_image = result.cover_image
                    
                    if hasattr(result, 'thumb'):
                        thumb = result.thumb
                    
                    releases.append({
                        "id": result.id,
                        "title": result.title,
                        "artist": artist,
                        "year": getattr(result, 'year', ''),
                        "cover_image": cover_image,
                        "thumb": thumb,
                        "format": [f['name'] for f in getattr(result, 'formats', [])] if hasattr(result, 'formats') else [],
                        "genre": list(getattr(result, 'genres', [])) if hasattr(result, 'genres') else [],
                        "uri": f"/release/{result.id}"  # Build URI for Discogs URL
                    })
                return {"success": True, "results": releases}
            return {"success": False, "error": "No results found"}
        except Exception as e:
            self.logger.error(f"Error searching Discogs: {e}")
            return {"success": False, "error": str(e)}
    
    def get_release_details(self, release_id: str):
        """Get detailed release info including tracklist, videos, and price data"""
        if not self.client:
            return {"success": False, "error": "Discogs client not initialized"}
        
        try:
            release = self.client.release(release_id)
            
            # Extract tracklist
            tracklist = []
            if hasattr(release, 'tracklist'):
                for track in release.tracklist:
                    tracklist.append({
                        "position": track.position,
                        "title": track.title,
                        "duration": track.duration if hasattr(track, 'duration') else ""
                    })
            
            # Get price data if available
            price_info = self._get_price_suggestions(release_id)
            market_stats = self._get_market_stats(release_id)
            release_overview = self._get_release_overview(release_id)
            
            # Fetch videos from Discogs REST API (more reliable for YouTube links)
            videos = self._get_release_videos(release_id)
            
            return {
                "success": True,
                "tracklist": tracklist,
                "artists": [{"name": artist.name} for artist in release.artists] if hasattr(release, 'artists') else [],
                "year": getattr(release, 'year', ''),
                "genres": list(getattr(release, 'genres', [])),
                "styles": list(getattr(release, 'styles', [])),
                "country": getattr(release, 'country', ''),
                "labels": [{"name": label.name} for label in release.labels] if hasattr(release, 'labels') else [],
                "price_info": price_info,
                "market_stats": market_stats,
                "release_overview": release_overview,
                "videos": videos
            }
            
        except Exception as e:
            self.logger.error(f"Error getting release details: {e}")
            return {"success": False, "error": str(e)}
    
    def _get_price_suggestions(self, release_id: str) -> dict:
        """Get price suggestions for a release"""
        try:
            import requests
            headers = {
                "Authorization": f"Discogs token={os.getenv('DISCOGS_TOKEN')}",
                "User-Agent": "CrateMate/1.0"
            }
            
            response = requests.get(
                f"https://api.discogs.com/marketplace/price_suggestions/{release_id}",
                headers=headers
            )
            
            if response.status_code == 200:
                price_data = response.json()
                
                # Calculate average price from available conditions
                prices = []
                for condition, data in price_data.items():
                    if isinstance(data, dict) and 'value' in data:
                        prices.append(data['value'])
                
                if prices:
                    avg_price = sum(prices) / len(prices)
                    currency = next(iter(price_data.values())).get('currency', 'USD')
                    
                    return {
                        "average_price": round(avg_price, 2),
                        "currency": currency,
                        "price_by_condition": price_data
                    }
            
            return None
            
        except Exception as e:
            self.logger.debug(f"Could not fetch price data: {e}")
            return None

    def _get_market_stats(self, release_id: str) -> dict:
        """Get marketplace statistics for a release (num_for_sale, lowest/median price)"""
        try:
            import requests
            headers = {
                "Authorization": f"Discogs token={os.getenv('DISCOGS_TOKEN')}",
                "User-Agent": "CrateMate/1.0"
            }

            # You can pass a currency via curr_abbr; default is marketplace default
            response = requests.get(
                f"https://api.discogs.com/marketplace/stats/{release_id}",
                headers=headers
            )

            if response.status_code == 200:
                data = response.json()
                lowest = data.get('lowest_price') or {}
                median = data.get('median_price') or {}
                return {
                    "num_for_sale": data.get('num_for_sale'),
                    "lowest_price": lowest.get('value'),
                    "median_price": median.get('value'),
                    "currency": lowest.get('currency') or median.get('currency')
                }

            return None

        except Exception as e:
            self.logger.debug(f"Could not fetch market stats: {e}")
            return None

    def _get_release_overview(self, release_id: str) -> dict:
        """Get release overview to extract 'copies from' info when available"""
        try:
            import requests
            headers = {
                "Authorization": f"Discogs token={os.getenv('DISCOGS_TOKEN')}",
                "User-Agent": "CrateMate/1.0"
            }
            r = requests.get(f"https://api.discogs.com/releases/{release_id}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                # The releases endpoint returns 'num_for_sale' and 'lowest_price'
                return {
                    "num_for_sale": data.get('num_for_sale'),
                    "lowest_price": (data.get('lowest_price', {}) or {}).get('value') if isinstance(data.get('lowest_price'), dict) else data.get('lowest_price'),
                    "currency": (data.get('lowest_price', {}) or {}).get('currency') if isinstance(data.get('lowest_price'), dict) else None
                }
        except Exception as e:
            self.logger.debug(f"Could not fetch release overview: {e}")
        return None

    def _get_release_videos(self, release_id: str):
        """Fetch YouTube videos listed on the Discogs release page via API"""
        try:
            import requests
            headers = {
                "Authorization": f"Discogs token={os.getenv('DISCOGS_TOKEN')}",
                "User-Agent": "CrateMate/1.0"
            }
            response = requests.get(
                f"https://api.discogs.com/releases/{release_id}",
                headers=headers
            )
            if response.status_code == 200:
                data = response.json()
                videos = []
                for v in data.get('videos', []) or []:
                    # Only include YouTube links
                    uri = v.get('uri', '')
                    if 'youtube.com' in uri or 'youtu.be' in uri:
                        videos.append({
                            'uri': uri,
                            'title': v.get('title', ''),
                            'description': v.get('description', ''),
                            'duration': v.get('duration', 0)
                        })
                return videos
        except Exception as e:
            self.logger.debug(f"Could not fetch release videos: {e}")
        return None

    async def fetch_artist_details(self, artist_name: str) -> dict:
        # This class will focus on text search, so we leave these async methods empty for now.
        pass

    async def fetch_album_details(self, artist_name: str, album_name: str) -> dict:
        pass
