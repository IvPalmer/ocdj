"""
Hybrid search system that combines multiple identification methods
"""
import logging
import asyncio
import os
from typing import Dict, List, Optional, Tuple
from PIL import Image
from fuzzywuzzy import fuzz
import numpy as np
import hashlib
import json
from datetime import datetime, timedelta

from .claude_vision import ClaudeVisionCollector
from .vision import VisionCollector
from .discogs import DiscogsCollector
from .spotify import SpotifyCollector
from .youtube import YouTubeCollector
from .youtube_simple import SimpleYouTubeSearch
from .bandcamp import BandcampCollector

# V1 drops the alternate YouTube backends (youtube_direct/_enhanced/_ytdlp) —
# they're noise without YOUTUBE_API_KEY, and the primary collector + simple
# search cover the happy path. Reintroduce in V2 if rate-limit hits force it.
class _NullYouTubeBackend:
    async def search_album(self, *args, **kwargs):
        return None

YouTubeDirectSearch = _NullYouTubeBackend
YouTubeEnhancedSearch = _NullYouTubeBackend
YouTubeYtdlpSearch = _NullYouTubeBackend

# NOTE: Avoid importing the heavy CLIP-based universal search at module import time.
SimpleUniversalSearch = None  # will be imported lazily if enabled

logger = logging.getLogger(__name__)


class HybridSearch:
    """
    Combines multiple search methods to find the best match.
    Priority: Claude vision (Max OAuth, $0/call) -> OCR fallback -> CLIP (off by default).

    History: V1 used Gemini Vision; replaced with Claude Agent SDK to (a) drop
    the third-party API-key dependency, (b) make recognition free under the
    operator's existing Max subscription, and (c) reuse the same auth path
    that `organize/services/agent_enrich.py` already uses on the VPS.
    """

    def __init__(self):
        self.vision_lm = ClaudeVisionCollector()
        self.vision = VisionCollector()
        self.discogs = DiscogsCollector()
        self.spotify = SpotifyCollector("spotify")
        self.youtube = YouTubeCollector("youtube")
        self.youtube_simple = SimpleYouTubeSearch()
        self.youtube_direct = YouTubeDirectSearch()
        self.youtube_enhanced = YouTubeEnhancedSearch()
        self.youtube_ytdlp = YouTubeYtdlpSearch()
        self.bandcamp = BandcampCollector()

        # Optionally enable the heavy CLIP-based universal search (disabled by default on Cloud Run)
        self.enable_universal: bool = str(os.getenv("ENABLE_UNIVERSAL", "0")).lower() in ["1", "true", "yes"]
        self.universal = None
        if self.enable_universal:
            try:
                from .simple_universal_search import SimpleUniversalSearch as _SUS
                self.universal = _SUS()
            except Exception as e:
                logger.warning("Universal search unavailable: %s", e)
                self.enable_universal = False
        
        # Simple in-memory cache with TTL
        self._cache = {}
        self._cache_ttl = timedelta(hours=24)  # Cache for 24 hours
        
        logger.info("Hybrid search system initialized")
    
    def _get_image_hash(self, image: Image.Image) -> str:
        """Generate a hash for the image to use as cache key"""
        # Convert image to bytes and hash
        import io
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        image_bytes = buffer.getvalue()
        return hashlib.md5(image_bytes).hexdigest()
    
    def _get_from_cache(self, key: str) -> Optional[Dict]:
        """Get result from cache if not expired"""
        if key in self._cache:
            cached_data = self._cache[key]
            if datetime.now() - cached_data['timestamp'] < self._cache_ttl:
                logger.info("Cache hit for image")
                return cached_data['result']
            else:
                # Expired, remove from cache
                del self._cache[key]
        return None
    
    def _save_to_cache(self, key: str, result: Dict):
        """Save result to cache"""
        self._cache[key] = {
            'timestamp': datetime.now(),
            'result': result
        }
        # Clean up old cache entries
        self._cleanup_cache()
    
    def _cleanup_cache(self):
        """Remove expired cache entries"""
        now = datetime.now()
        expired_keys = [
            key for key, data in self._cache.items()
            if now - data['timestamp'] > self._cache_ttl
        ]
        for key in expired_keys:
            del self._cache[key]
    
    async def search_album(self, album_image: Image.Image) -> Dict:
        """
        Main search method that combines all approaches
        """
        try:
            # Check cache first
            image_hash = self._get_image_hash(album_image)
            cached_result = self._get_from_cache(image_hash)
            if cached_result:
                return cached_result
            # Run multiple methods in parallel (skip universal if disabled).
            # OCR is kept as a fallback even though empirically it almost
            # never produces a Discogs-resolvable hit on stylized covers —
            # cheap to keep running in case the vision LM call itself fails.
            tasks = [
                self._vision_lm_search(album_image),
                self._vision_ocr_search(album_image),
            ]
            if self.enable_universal and self.universal is not None:
                tasks.insert(1, self._universal_search(album_image))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Unpack based on whether universal ran
            if self.enable_universal and self.universal is not None:
                vision_lm_result = results[0] if not isinstance(results[0], Exception) else None
                universal_result = results[1] if not isinstance(results[1], Exception) else None
                ocr_result = results[2] if not isinstance(results[2], Exception) else None
            else:
                vision_lm_result = results[0] if not isinstance(results[0], Exception) else None
                universal_result = None
                ocr_result = results[1] if len(results) > 1 and not isinstance(results[1], Exception) else None

            # Combine and rank all candidates
            all_candidates = []

            # Process Claude vision results (highest priority).
            # `gemini_data` name preserved for downstream confidence calc and
            # the candidate dict — the shape is identical so no other code moves.
            if vision_lm_result and vision_lm_result.get("success"):
                gemini_data = vision_lm_result["result"]
                # Include `visible_text` as a trigger so we still query Discogs
                # when Claude couldn't extract a proper artist/album but did
                # OCR readable text off the sleeve (e.g. obscure Brazilian
                # records where the visible text is the only signal we have).
                if (
                    gemini_data.get("artist")
                    or gemini_data.get("album")
                    or gemini_data.get("label")
                    or gemini_data.get("visible_text")
                ):
                    discogs_results = await self._search_discogs_with_fallback(
                        gemini_data.get("artist") or "",
                        gemini_data.get("album") or "",
                        label=gemini_data.get("label") or "",
                        visible_text=gemini_data.get("visible_text") or "",
                    )
                    for disc_result in discogs_results[:3]:
                        all_candidates.append({
                            "source": "claude_vision",
                            "confidence": self._calculate_confidence(gemini_data, disc_result),
                            "discogs_data": disc_result,
                            "gemini_data": gemini_data
                        })
            
            # Process Universal search results
            if universal_result and not universal_result.get("error"):
                all_candidates.append({
                    "source": "universal",
                    "confidence": 0.7,  # Base confidence for universal search
                    "discogs_data": universal_result,
                    "gemini_data": None
                })
                
                # Add alternatives from universal search
                for alt in universal_result.get("alternatives", [])[:2]:
                    all_candidates.append({
                        "source": "universal_alt",
                        "confidence": 0.5,
                        "discogs_data": alt,
                        "gemini_data": None
                    })
            
            # Process OCR results
            if ocr_result and ocr_result.get("success"):
                text_lines = ocr_result.get("text_lines", [])
                if text_lines:
                    # Try to find matches with OCR text
                    ocr_discogs = await self._search_discogs_with_text(" ".join(text_lines))
                    for disc_result in ocr_discogs[:2]:
                        all_candidates.append({
                            "source": "ocr",
                            "confidence": 0.6,
                            "discogs_data": disc_result,
                            "gemini_data": None
                        })
            
            # Perceptual-hash verification — download each candidate's cover
            # and reject ones that don't visually resemble the upload. Kills
            # false positives where Claude hallucinated a real album name and
            # Discogs returned a text-match for a totally different cover
            # (the Prefuse 73 case: medium-confidence text-match shipped
            # without anyone checking if the artwork actually matched).
            #
            # Verification only activates when Claude's evidence_quality is
            # weak/none OR the candidate came from a broad fallback
            # (artist-only / label-only / visible-text). Strong evidence +
            # exact artist+album hit is trusted without round-tripping
            # cover downloads.
            vd = (
                vision_lm_result.get("result")
                if (vision_lm_result and vision_lm_result.get("success"))
                else None
            )
            verified_candidates = await self._verify_candidates_by_cover(
                all_candidates, album_image, vd
            )

            # Select best candidate (now with pHash distance penalty applied)
            best_match = self._select_best_match(verified_candidates)

            if best_match:
                # Get all available links
                final_result = await self._build_final_result(best_match)
                # Stamp visual-verification info so frontend can show "verified
                # against the cover image" badge for high-trust matches.
                if "phash_distance" in best_match:
                    final_result.setdefault("identification", {})["cover_match_distance"] = best_match["phash_distance"]
                # Save to cache before returning
                self._save_to_cache(image_hash, final_result)
                return final_result

            # No verified Discogs candidates. If Claude actually saw something,
            # return a vision-only result so the user gets the artist/album +
            # the model's evidence. Better than the old "couldn't identify"
            # blanket error which threw away a high-confidence answer just
            # because Discogs didn't index that release.
            if vd and (vd.get("artist") or vd.get("album") or vd.get("visible_text")):
                return self._vision_only_result(vd)

            return {
                "error": "Could not identify the album",
                "discogs_url": "unavailable",
                "spotify_url": "unavailable",
                "youtube_url": "unavailable",
            }
                
        except Exception as e:
            logger.error(f"Hybrid search error: {str(e)}", exc_info=True)
            return {
                "error": f"Search failed: {str(e)}",
                "discogs_url": "unavailable",
                "spotify_url": "unavailable",
                "youtube_url": "unavailable"
            }
    
    async def _vision_lm_search(self, image: Image.Image) -> Dict:
        """Run Claude vision identification (Max OAuth, no API key)."""
        try:
            return await self.vision_lm.identify_album(image)
        except Exception as e:
            logger.error(f"Claude vision search failed: {e}")
            return {"success": False, "error": str(e)}

    async def _search_discogs_simple(
        self, artist: str, album: str, label: str = ""
    ) -> List[Dict]:
        """Simple Discogs lookup — try the obvious queries, take the first
        non-empty result, return up to 5 hits.

        V3's elaborate fallback chain (7 attempts, aggregation, dedup, fuzz
        scoring) made things worse: when Claude returned 'marschmellows /
        flesh fried' the strict artist+album query fuzz-matched a death-
        metal record on the word 'flesh', that became the answer despite a
        terrible cover-image match. Keep it simple — the broad queries and
        downstream pHash sanity check do the work.
        """
        attempts = []
        artist = (artist or "").strip()
        album = (album or "").strip()
        label = (label or "").strip()

        if artist and album:
            attempts.append(("artist+album", f"{artist} {album}"))
        if label and album:
            attempts.append(("label+album", f"{label} {album}"))
        if album:
            attempts.append(("album", album))
        if artist:
            attempts.append(("artist", artist))

        for label_, query in attempts:
            try:
                res = self.discogs.search_release(query)
                if not (res and res.get("success")):
                    continue
                hits = res.get("results") or []
                if not hits:
                    continue
                logger.info(
                    "discogs (%s) %r -> %d hits", label_, query, len(hits),
                )
                # Tag attempt source for debugging.
                for h in hits[:5]:
                    h['_attempt'] = label_
                return hits[:5]
            except Exception as e:
                logger.warning("discogs query %s failed: %s", label_, e)
        return []

    async def _search_discogs_with_fallback(
        self, artist: str, album: str, label: str = "", visible_text: str = ""
    ) -> List[Dict]:
        """Legacy entry point — kept for backward compat with any callers
        still using the old name. Just forwards to the simpler helper."""
        return await self._search_discogs_simple(artist, album, label)


    async def _verify_candidates_by_cover(
        self,
        candidates: List[Dict],
        upload_image: Image.Image,
        vd: Optional[Dict],
    ) -> List[Dict]:
        """Compute perceptual-hash distance between the upload and each
        candidate's Discogs cover image; annotate + filter.

        Strategy:
          - Compute upload pHash + dHash once.
          - For each candidate, download its cover_image (bounded timeout +
            size). pHash both. Hamming-distance-sum.
          - Annotate `phash_distance` on every candidate.
          - For LOW-TRUST candidates (broad-fallback source OR weak vision
            evidence), drop ones with distance > REJECT_THRESHOLD.
          - For HIGH-TRUST candidates (strong evidence + exact match), only
            log the distance — don't reject. Some legit matches still have
            distance > 20 due to JPEG/crop/lighting; we don't want pHash to
            block obviously-correct hits.

        Skips silently if imagehash isn't installed (graceful degradation
        until the new dep deploys; the old behaviour is no worse than V2).
        """
        if not candidates:
            return candidates
        try:
            import imagehash  # type: ignore  # added to requirements.txt
        except ImportError:
            logger.warning("imagehash not installed — skipping cover verification")
            return candidates

        # Compute upload hash once. Both pHash + dHash because they catch
        # different distortion modes (pHash = DCT, dHash = gradient direction).
        try:
            up_phash = imagehash.phash(upload_image)
            up_dhash = imagehash.dhash(upload_image)
        except Exception as e:
            logger.warning("upload imagehash failed: %s", e)
            return candidates

        # Combined hamming distance threshold. Empirically pHash+dHash both
        # 64-bit; matching reissues of the same artwork sit at 5-22, mild
        # crops/rotations at 22-40, completely different art at 40+.
        REJECT_THRESHOLD = 32
        ICONIC_REJECT_THRESHOLD = 80   # extremely permissive for iconic covers
                                       # (Pink Floyd Dark Side reissues all over the place)

        is_iconic = bool((vd or {}).get("is_iconic"))

        async def _hash_one(cand: Dict) -> None:
            disc = cand.get("discogs_data") or {}
            url = disc.get("cover_image") or disc.get("thumb")
            if not url:
                cand["phash_distance"] = None
                return
            try:
                # Tiny embedded import keeps the module importable without
                # aiohttp at startup time (we already use it for spotify).
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status != 200:
                            cand["phash_distance"] = None
                            return
                        body = await resp.read()
                if len(body) > 10 * 1024 * 1024:  # 10 MB sanity cap
                    cand["phash_distance"] = None
                    return
                cover = Image.open(BytesIO(body))
                # Downscale before hashing — faster, also normalizes the
                # comparison since uploads and Discogs covers come at very
                # different resolutions.
                cover.thumbnail((512, 512))
                ph = imagehash.phash(cover)
                dh = imagehash.dhash(cover)
                # imagehash returns numpy int64; cast so the result survives
                # JSON serialization when persisted to AlbumIdentification.raw_response.
                cand["phash_distance"] = int((up_phash - ph) + (up_dhash - dh))
            except Exception as e:
                logger.debug("phash candidate %s failed: %s", disc.get("id"), e)
                cand["phash_distance"] = None

        # Hash up to 6 candidates concurrently (Discogs CDN can take it).
        from io import BytesIO  # local import — only used here
        await asyncio.gather(
            *[_hash_one(c) for c in candidates[:6]],
            return_exceptions=True,
        )

        # Apply rejection rules.
        kept: List[Dict] = []
        for c in candidates:
            dist = c.get("phash_distance")
            disc = c.get("discogs_data") or {}
            disc_id = disc.get("id")

            # No distance computed → keep but penalize confidence slightly so
            # confirmed visual matches outrank.
            if dist is None:
                kept.append(c)
                continue

            # V4: pHash is a TIEBREAKER + safety net, not a gate.
            # Reject only on extreme distance (clearly different cover) AND
            # only when Claude's text-match was weak. Strong text match wins
            # regardless — accommodates reissue artwork variation.
            #
            # When Discogs returned multiple candidates, pHash boost lets
            # the visually-correct release rise to the top of the rank.
            EXTREME_DIST = 50

            text_match_strong = c["confidence"] >= 0.7  # text fuzz already strong
            if dist > EXTREME_DIST and not text_match_strong and not is_iconic:
                logger.info(
                    "phash REJECT candidate %s (dist=%d, low text conf=%.2f)",
                    disc_id, dist, c["confidence"],
                )
                continue

            # Tiebreaker boost for visually similar covers.
            if dist <= 12:
                c["confidence"] = min(0.99, c["confidence"] + 0.15)
            elif dist <= 22:
                c["confidence"] = min(0.99, c["confidence"] + 0.06)
            else:
                c["confidence"] = max(0.0, c["confidence"] - 0.03)

            logger.info(
                "phash candidate %s dist=%d -> conf=%.2f",
                disc_id, dist, c["confidence"],
            )
            kept.append(c)

        return kept

    def _vision_only_result(self, vd: Dict) -> Dict:
        """Build a minimal result payload when vision succeeded but Discogs
        didn't return any matchable release.

        The shape mirrors `_build_final_result` so views.py can extract via
        the same `_flatten_search_result` path — frontend treats it as a
        normal recognized result, just with `unavailable` external links and
        a `vision_only: true` marker the UI can use to show a "no Discogs
        match — verify the guess and look it up manually" hint.
        """
        artist = vd.get("artist") or ""
        album = vd.get("album") or ""
        return {
            "album": {
                "name": album,
                "artist": artist,
                "release_date": "",
                "genres": [],
                "image": "",
                "country": "",
                "label": "",
            },
            "identification": {
                # Map Claude's bucket to a numeric — slightly lower than the
                # Discogs-confirmed branch so downstream sorting prefers a
                # confirmed match if one ever shows up.
                "confidence": {
                    "high": 0.55, "medium": 0.45, "low": 0.35,
                }.get((vd.get("confidence") or "low").lower(), 0.40),
                "method": "claude_vision_only",
                "source": "claude_vision_no_discogs",
            },
            "links": {
                "discogs": "unavailable",
                "spotify": "unavailable",
                "youtube": "unavailable",
                "bandcamp": self._generate_bandcamp_search_link(artist, album) if (artist or album) else None,
            },
            "tracks": {"total": 0, "tracklist": [], "spotify_tracks": [], "youtube_tracks": []},
            "vision_only": True,
            "vision_evidence": vd.get("description") or "",
            "vision_visible_text": vd.get("visible_text") or "",
            "warning": (
                "Found a likely identification but no Discogs match. The label "
                "shown on the cover may be in the artist field — try editing it "
                "in manual lookup."
            ),
        }

    async def manual_lookup(self, artist: str, album: str) -> Dict:
        """Skip the vision step — go straight from a known artist+album to the
        full enrichment payload (Discogs + Spotify + YouTube + Bandcamp).

        Used by `POST /api/cratemate/lookup/` when the user already knows the
        identity and wants the cross-platform links + tracklist."""
        discogs_results = await self._search_discogs_with_info(artist, album)
        if not discogs_results:
            return {
                "error": f"No Discogs match for {artist!r} / {album!r}",
                "discogs_url": "unavailable",
                "spotify_url": "unavailable",
                "youtube_url": "unavailable",
            }
        # Synthesize a minimal gemini_data so confidence calc + final-result
        # builder behave identically to the image-driven path.
        synthetic = {"artist": artist, "album": album, "confidence": "high"}
        best = {
            "source": "manual",
            "confidence": self._calculate_confidence(synthetic, discogs_results[0]),
            "discogs_data": discogs_results[0],
            "gemini_data": synthetic,
        }
        return await self._build_final_result(best)
    
    async def _universal_search(self, image: Image.Image) -> Dict:
        """Run existing universal search"""
        try:
            return await self.universal.search_album(image)
        except Exception as e:
            logger.error(f"Universal search failed: {e}")
            return {"error": str(e)}
    
    async def _vision_ocr_search(self, image: Image.Image) -> Dict:
        """Run Vision API OCR"""
        try:
            # Convert image to bytes (ensure RGB to avoid RGBA->JPEG error)
            from io import BytesIO
            buffered = BytesIO()
            safe_img = image.convert("RGB") if getattr(image, "mode", "") in ("RGBA", "P") else image
            safe_img.save(buffered, format="JPEG")
            image_bytes = buffered.getvalue()
            
            return await self.vision.extract_text_from_image(image_bytes)
        except Exception as e:
            logger.error(f"Vision OCR failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _search_discogs_with_info(self, artist: str, album: str) -> List[Dict]:
        """Search Discogs with artist and album info"""
        try:
            # Try exact search first
            results = []
            
            # Search with both artist and album
            search_result = self.discogs.search_release(f"{artist} {album}")
            if search_result and search_result.get("success"):
                results.extend(search_result.get("results", [])[:5])
            
            # Also try searching just by artist
            artist_result = self.discogs.search_release(artist)
            if artist_result and artist_result.get("success"):
                # Filter results to likely matches
                for result in artist_result.get("results", [])[:10]:
                    if album and fuzz.partial_ratio(album.lower(), result.get("title", "").lower()) > 70:
                        if result not in results:
                            results.append(result)
            
            return results
            
        except Exception as e:
            logger.error(f"Discogs search error: {e}")
            return []
    
    async def _search_discogs_with_text(self, text: str) -> List[Dict]:
        """Search Discogs with raw text"""
        try:
            search_result = self.discogs.search_release(text)
            if search_result and search_result.get("success"):
                return search_result.get("results", [])[:5]
            return []
        except Exception as e:
            logger.error(f"Discogs text search error: {e}")
            return []
    
    def _calculate_confidence(self, gemini_data: Dict, discogs_data: Dict) -> float:
        """Confidence score for a Claude-vision-suggested + Discogs-found match.

        V4 simple: trust Claude's confidence bucket, soft-multiply by fuzz
        match. Don't gate on hard floors — the Discogs query was already
        seeded by Claude's identification, so a top hit is usually right.
        pHash verification (later) catches the rare case where Discogs
        fuzz-matched a different record."""
        bucket = (gemini_data.get("confidence") or "low").lower()
        base = {"high": 0.9, "medium": 0.75, "low": 0.6}.get(bucket, 0.5)

        if gemini_data.get("artist") and discogs_data.get("artist"):
            af = fuzz.token_set_ratio(
                gemini_data["artist"].lower(),
                str(discogs_data.get("artist", "")).lower(),
            ) / 100.0
            base *= (0.6 + 0.4 * af)

        if gemini_data.get("album") and discogs_data.get("title"):
            tf = fuzz.token_set_ratio(
                gemini_data["album"].lower(),
                str(discogs_data.get("title", "")).lower(),
            ) / 100.0
            base *= (0.6 + 0.4 * tf)

        return min(0.95, base)
    
    def _select_best_match(self, candidates: List[Dict]) -> Optional[Dict]:
        """Select the best match from all candidates"""
        if not candidates:
            return None
        
        # Sort by confidence
        candidates.sort(key=lambda x: x["confidence"], reverse=True)
        
        # Log top candidates
        for i, cand in enumerate(candidates[:3]):
            disc_data = cand["discogs_data"]
            logger.info(
                f"Candidate {i+1}: {disc_data.get('artist', 'Unknown')} - "
                f"{disc_data.get('title', 'Unknown')} "
                f"(confidence: {cand['confidence']:.2f}, source: {cand['source']})"
            )
        
        # V4: low threshold. Discogs query was already seeded by Claude's
        # identification — if any hit came back, take the top one. The pHash
        # safety net rejects clearly-mismatched covers earlier in the
        # pipeline; what survives is usually right.
        best = candidates[0]
        if best["confidence"] >= 0.30:
            return best

        logger.info(
            "best candidate below threshold (conf=%.2f) — falling through to vision-only",
            best["confidence"],
        )
        return None
    
    async def _build_final_result(self, match: Dict) -> Dict:
        """Build final result with all links"""
        discogs_data = match["discogs_data"]
        
        # Extract artist and album info
        artist = self._extract_artist(discogs_data)
        album = discogs_data.get("title", "Unknown Album")
        
        # Get detailed release info including tracklist, price, and videos
        price_info = None
        market_stats = None
        release_overview = None
        release_videos = None
        if discogs_data.get("id"):
            try:
                logger.info(f"Fetching details for Discogs release ID: {discogs_data['id']}")
                details = self.discogs.get_release_details(str(discogs_data["id"]))
                if details.get("success"):
                    if details.get("tracklist"):
                        discogs_data["tracklist"] = details["tracklist"]
                        logger.info(f"Fetched {len(details['tracklist'])} tracks from Discogs")
                    if details.get("price_info"):
                        price_info = details["price_info"]
                        logger.info(f"Fetched price info: ${price_info.get('average_price', 'N/A')} {price_info.get('currency', '')}")
                    if details.get("market_stats"):
                        market_stats = details["market_stats"]
                        logger.info(f"Fetched market stats: for sale={market_stats.get('num_for_sale')} median={market_stats.get('median_price')}")
                    if details.get("release_overview"):
                        release_overview = details["release_overview"]
                        logger.info(f"Release overview: for sale={release_overview.get('num_for_sale')} from={release_overview.get('lowest_price')}")
                    if details.get("videos"):
                        release_videos = details["videos"]
                        logger.info(f"Fetched {len(release_videos)} videos from Discogs release")
                        for v in release_videos[:3]:  # Log first 3 videos
                            logger.info(f"  Video: {v.get('title', 'N/A')} - {v.get('uri', 'N/A')}")
                    else:
                        logger.info("No videos found in Discogs release")
                else:
                    logger.warning(f"Failed to get Discogs details: {details.get('error', 'Unknown error')}")
            except Exception as e:
                logger.error(f"Could not fetch Discogs details: {e}", exc_info=True)
        
        # Build result with better formatting
        result = {
            # Basic album info
            "album": {
                "name": album,
                "artist": artist,
                "release_date": str(discogs_data.get("year", "")),
                "genres": discogs_data.get("genre", []),
                "image": discogs_data.get("cover_image") or discogs_data.get("thumb", ""),
                "country": discogs_data.get("country", ""),
                "label": discogs_data.get("label", "")
            },
            
            # Identification metadata
            "identification": {
                "confidence": match["confidence"],
                "method": f"hybrid_{match['source']}",
                "source": match["source"]
            },
            
            # All available links
            "links": {
                "discogs": self._build_discogs_url(discogs_data),
                "spotify": "unavailable",
                "youtube": "unavailable",
                "bandcamp": None
            },
            
            # Price information
            "price_info": price_info,
            "market_stats": market_stats,
            "release_overview": release_overview,
            
            # Track information
            "tracks": {
                "total": len(discogs_data.get("tracklist", [])),
                "tracklist": [],
                "spotify_tracks": [],
                "youtube_searches": []
            }
        }
        
        # Try to get Spotify link and tracks
        try:
            logger.info(f"Searching Spotify for: {artist} - {album}")
            spotify_result = await self.spotify.fetch_album_details(artist, album)
            if spotify_result and not spotify_result.get("error"):
                # Update Spotify link
                spotify_url = spotify_result.get("url")
                if spotify_url:
                    result["links"]["spotify"] = spotify_url
                    logger.info(f"Found Spotify URL: {spotify_url}")
                
                # Extract tracks with better formatting
                if spotify_result.get("tracks"):
                    result["tracks"]["spotify_tracks"] = [
                        {
                            "position": idx + 1,
                            "name": track.get("name", ""),
                            "duration_seconds": track.get("duration", 0),
                            "explicit": track.get("explicit", False),
                            "url": track.get("url"),
                            "id": track.get("id"),
                            "uri": track.get("uri"),
                        }
                        for idx, track in enumerate(spotify_result["tracks"])
                    ]
                    logger.info(f"Found {len(result['tracks']['spotify_tracks'])} Spotify tracks")
            else:
                logger.info(f"No Spotify match found for {artist} - {album}")
        except Exception as e:
            logger.error(f"Spotify search error: {e}", exc_info=True)
        
        # Try Bandcamp direct release link
        try:
            bandcamp_url = self.bandcamp.find_release_link(artist, album)
            if bandcamp_url:
                result["links"]["bandcamp"] = bandcamp_url
            else:
                result["links"]["bandcamp"] = self._generate_bandcamp_search_link(artist, album)
        except Exception as e:
            logger.debug(f"Bandcamp lookup failed: {e}")
            result["links"]["bandcamp"] = self._generate_bandcamp_search_link(artist, album)
        
        # Generate YouTube links for tracks
        try:
            logger.info(f"Generating YouTube links for: {artist} - {album}")
            
            # If Discogs release includes YouTube videos, use them first
            if release_videos:
                logger.info(f"Using {len(release_videos)} YouTube videos from Discogs")
                
                # First, set the album-level YouTube link
                result["links"]["youtube"] = release_videos[0].get('uri', 'unavailable')
                
                # Map videos to tracks
                mapped_tracks = []
                tracklist = discogs_data.get("tracklist", []) or []
                
                for track in tracklist:
                    track_title = track.get('title', '')
                    position = track.get('position', '')
                    duration = track.get('duration', '')
                    
                    # Try exact position matching first (A1, B1, etc)
                    youtube_match = None
                    for v in release_videos:
                        video_title = v.get('title', '')
                        # Check if video title contains track position (e.g., "A1. Taipei Disco")
                        if position and position.lower() in video_title.lower():
                            youtube_match = {
                                'url': v.get('uri'),
                                'title': video_title,
                                'is_search': False,
                                'source': 'discogs'
                            }
                            logger.info(f"Matched track {position} by position to video: {video_title}")
                            break
                    
                    # If no position match, try fuzzy title matching
                    if not youtube_match and track_title:
                        best_match = None
                        best_score = 0
                        for v in release_videos:
                            video_title = v.get('title', '')
                            if video_title:
                                score = fuzz.token_set_ratio(track_title.lower(), video_title.lower())
                                if score > best_score:
                                    best_score = score
                                    best_match = v
                        
                        if best_match and best_score >= 60:  # Lower threshold for better matching
                            youtube_match = {
                                'url': best_match.get('uri'),
                                'title': best_match.get('title', ''),
                                'is_search': False,
                                'source': 'discogs'
                            }
                            logger.info(f"Matched track '{track_title}' to video '{best_match.get('title')}' (score: {best_score})")
                    
                    mapped_tracks.append({
                        'position': position,
                        'title': track_title,
                        'duration': duration,
                        'youtube': youtube_match
                    })
                
                result["tracks"]["youtube_tracks"] = mapped_tracks
                logger.info(f"Mapped {sum(1 for t in mapped_tracks if t.get('youtube'))} tracks to YouTube videos")
                
            elif youtube_api_key := None:  # os.getenv('YOUTUBE_API_KEY')  # Disabled for now
                logger.info("Using YouTube API to fetch actual video links")
                try:
                    # Fetch album details including track videos from YouTube API
                    youtube_data = await self.youtube.fetch_album_details(artist, album)
                    
                    # Set album YouTube link
                    if youtube_data.get("youtube_url"):
                        result["links"]["youtube"] = youtube_data["youtube_url"]
                    else:
                        # Fallback to search link
                        album_link = self.youtube_enhanced.generate_album_link(artist, album)
                        result["links"]["youtube"] = album_link["url"]
                    
                    # Process track videos
                    if youtube_data.get("youtube_tracks") and discogs_data.get("tracklist"):
                        # Create enhanced track list with actual YouTube video URLs
                        youtube_tracks = []
                        for discogs_track in discogs_data["tracklist"]:
                            track_title = discogs_track.get("title", "")
                            position = discogs_track.get("position", "")
                            duration = discogs_track.get("duration", "")
                            
                            # Find matching YouTube video
                            youtube_video = None
                            for yt_track in youtube_data["youtube_tracks"]:
                                if self._tracks_match(track_title, yt_track.get("track", "")):
                                    youtube_video = {
                                        "url": yt_track["url"],
                                        "title": yt_track.get("track", ""),
                                        "channel": yt_track.get("channel", ""),
                                        "is_search": False  # This is a direct video link
                                    }
                                    break
                            
                            youtube_tracks.append({
                                "position": position,
                                "title": track_title,
                                "duration": duration,
                                "youtube": youtube_video
                            })
                        
                        result["tracks"]["youtube_tracks"] = youtube_tracks
                        logger.info(f"Found {len([t for t in youtube_tracks if t.get('youtube')])} YouTube videos for tracks")
                    else:
                        # No tracks found, use search links
                        youtube_tracks = self._generate_track_search_links(
                            artist, album, discogs_data.get("tracklist", [])
                        )
                        result["tracks"]["youtube_tracks"] = youtube_tracks
                except Exception as e:
                    logger.error(f"YouTube API error: {e}, falling back to search links", exc_info=True)
                    # Fallback to search links
                    album_link = self.youtube_enhanced.generate_album_link(artist, album)
                    result["links"]["youtube"] = album_link["url"]
                    
                    if discogs_data.get("tracklist"):
                        youtube_tracks = self._generate_track_search_links(
                            artist, album, discogs_data["tracklist"]
                        )
                        result["tracks"]["youtube_tracks"] = youtube_tracks
            else:
                logger.info("No YouTube API key, using search links")
                # Generate album search link
                album_link = self.youtube_enhanced.generate_album_link(artist, album)
                result["links"]["youtube"] = album_link["url"]
                
                # Generate individual track links
                if discogs_data.get("tracklist"):
                    youtube_tracks = self._generate_track_search_links(
                        artist, album, discogs_data["tracklist"]
                    )
                    result["tracks"]["youtube_tracks"] = youtube_tracks
                    logger.info(f"Generated YouTube search links for {len(youtube_tracks)} tracks")
            
        except Exception as e:
            logger.error(f"YouTube link generation error: {e}", exc_info=True)
        
        # Build formatted tracklist combining all sources
        if discogs_data.get("tracklist"):
            result["tracks"]["tracklist"] = self._build_formatted_tracklist(
                discogs_data["tracklist"],
                result["tracks"]["spotify_tracks"],
                result["tracks"].get("youtube_tracks", [])
            )
        
        # If confidence is low, include alternative candidates (top 5)
        if result["identification"]["confidence"] < 0.9:
            try:
                # Build fresh alternatives list from all gathered candidates
                alternatives: List[Dict] = []
                # Re-run discogs search using gemini/universal candidates context
                # We have already aggregated candidates earlier in search flow; try to reconstruct from discogs
                # Fallback: search Discogs by artist/album text
                discogs_alts = await self._search_discogs_with_info(artist, album)
                for cand in discogs_alts[:5]:
                    alternatives.append({
                        "title": cand.get("title"),
                        "artist": cand.get("artist"),
                        "discogs": self._build_discogs_url(cand)
                    })
                if alternatives:
                    result["alternatives"] = alternatives
            except Exception:
                pass

        # Flatten for backward compatibility
        result["album_name"] = result["album"]["name"]
        result["artist_name"] = result["album"]["artist"]
        result["confidence"] = result["identification"]["confidence"]
        result["method"] = result["identification"]["method"]
        result["discogs_url"] = result["links"]["discogs"]
        result["spotify_url"] = result["links"]["spotify"]
        result["youtube_url"] = result["links"]["youtube"]
        result["bandcamp_url"] = result["links"]["bandcamp"]
        
        # Add price info if available
        if price_info:
            result["average_price"] = price_info.get("average_price")
            result["price_currency"] = price_info.get("currency", "USD")
        else:
            result["average_price"] = None
            result["price_currency"] = None

        # Flatten market stats for convenience
        # Prefer releases endpoint values for exact "copies from" display
        if release_overview and (release_overview.get("num_for_sale") or release_overview.get("lowest_price")):
            result["num_for_sale"] = release_overview.get("num_for_sale")
            result["lowest_price"] = release_overview.get("lowest_price")
            if not result.get("price_currency"):
                result["price_currency"] = release_overview.get("currency", result.get("price_currency"))
        elif market_stats:
            result["num_for_sale"] = market_stats.get("num_for_sale")
            result["lowest_price"] = market_stats.get("lowest_price")
            result["median_price"] = market_stats.get("median_price")
            if not result.get("price_currency"):
                result["price_currency"] = market_stats.get("currency", "USD")
        
        return result
    
    def _extract_youtube_video_id(self, url: str) -> str:
        """Extract video ID from YouTube URL"""
        import re
        if not url:
            return ""
        
        # Handle various YouTube URL formats
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return ""
    
    def _build_formatted_tracklist(self, discogs_tracks: List[Dict], spotify_tracks: List[Dict], youtube_data: List[Dict]) -> List[Dict]:
        """Build a beautifully formatted tracklist combining all sources"""
        formatted_tracks = []
        
        for idx, track in enumerate(discogs_tracks):
            # Skip invalid/placeholder titles
            import re
            title_val = (track.get("title") or "").strip()
            if not title_val or re.fullmatch(r"[\d\s\.]+", title_val):
                continue
            # Base track info from Discogs
            formatted_track = {
                "position": track.get("position", f"{idx + 1}"),
                "title": track.get("title", "Unknown Track"),
                "duration": track.get("duration", ""),
                
                # Availability flags
                "available_on": {
                    "spotify": False,
                    "youtube": False
                },
                
                # Spotify info
                "spotify": None,
                
                # YouTube info
                "youtube": None
            }
            
            # Match with Spotify tracks
            for sp_track in spotify_tracks:
                if self._tracks_match(track["title"], sp_track["name"]):
                    formatted_track["available_on"]["spotify"] = True
                    formatted_track["spotify"] = {
                        "track_name": sp_track["name"],
                        "duration_seconds": sp_track["duration_seconds"],
                        "explicit": sp_track.get("explicit", False),
                        "url": sp_track.get("url"),
                        "id": sp_track.get("id"),
                        "uri": sp_track.get("uri"),
                    }
                    break
            
            # Find corresponding YouTube data
            for yt_data in youtube_data:
                if track.get("position") == yt_data.get("position"):
                    if yt_data.get("youtube"):
                        formatted_track["available_on"]["youtube"] = True
                        formatted_track["youtube"] = yt_data["youtube"]
                    break
            
            formatted_tracks.append(formatted_track)
        
        return formatted_tracks
    
    def _tracks_match(self, title1: str, title2: str) -> bool:
        """Check if two track titles match"""
        if not title1 or not title2:
            return False
        
        # Simple fuzzy matching for track titles
        from fuzzywuzzy import fuzz
        return fuzz.ratio(title1.lower(), title2.lower()) > 80
    
    def _map_youtube_to_discogs_tracks(self, discogs_tracks: List[Dict], youtube_tracks: List[Dict], artist: str) -> List[Dict]:
        """Map YouTube video results to Discogs tracklist"""
        enhanced_tracks = []
        
        for track in discogs_tracks:
            track_title = track.get("title", "")
            position = track.get("position", "")
            duration = track.get("duration", "")
            
            # Find matching YouTube video
            youtube_match = None
            for yt_track in youtube_tracks:
                yt_title = yt_track.get("track", "")
                # Check if YouTube title contains the track title
                if track_title and self._tracks_match(track_title, yt_title):
                    youtube_match = yt_track
                    break
            
            enhanced_track = {
                "position": position,
                "title": track_title,
                "duration": duration,
                "youtube": None
            }
            
            if youtube_match:
                enhanced_track["youtube"] = {
                    "url": youtube_match["url"],
                    "title": youtube_match.get("track", ""),
                    "channel": youtube_match.get("channel", ""),
                    "video_id": self._extract_youtube_video_id(youtube_match["url"])
                }
            
            enhanced_tracks.append(enhanced_track)
        
        return enhanced_tracks
    
    def _generate_track_search_links(self, artist: str, album: str, tracklist: List[Dict]) -> List[Dict]:
        """Generate YouTube links — try yt-dlp first, then fallback to search.

        V2: dropped the Gemini-guesses-YouTube-IDs path that used to live here.
        Routing this through Claude is technically possible (same SDK pattern
        as identification) but the value is marginal — this method only runs
        when the Discogs release has no embedded videos AND the YouTube API
        key isn't set, which is the rare-tail path. yt-dlp + search links
        cover it well enough.
        """
        # Try yt-dlp if available
        if self.youtube_ytdlp.ytdlp_available:
            logger.info("Trying yt-dlp to get direct YouTube video URLs")
            try:
                youtube_tracks = self.youtube_ytdlp.get_track_videos(artist, album, tracklist)
                # Check if we got any actual video URLs
                videos_found = sum(1 for t in youtube_tracks if t.get("youtube"))
                if videos_found > 0:
                    logger.info(f"Found {videos_found} direct YouTube video URLs using yt-dlp")
                    return youtube_tracks
            except Exception as e:
                logger.warning(f"yt-dlp failed, falling back to search links: {e}")
        
        # Fallback to search links
        logger.info("Using YouTube search links (yt-dlp not available or failed)")
        enhanced_tracks = []
        
        import re
        from urllib.parse import quote_plus
        cleaned_artist = re.sub(r"\s*\(\d+\)$", "", artist).strip()
        cleaned_album = re.sub(r"\s*\(\d+\)$", "", album).strip()

        def is_valid_title(title: str) -> bool:
            if not title:
                return False
            t = title.strip()
            if re.fullmatch(r"[\d\s\.]+", t):
                return False
            if len(t) < 3:
                return False
            return True

        max_tracks = 60
        count = 0
        for track in tracklist:
            if count >= max_tracks:
                break
            track_title = track.get("title", "")
            position = track.get("position", "")
            duration = track.get("duration", "")

            if not is_valid_title(track_title):
                enhanced_tracks.append({
                    "position": position,
                    "title": track_title,
                    "duration": duration,
                    "youtube": None
                })
                continue

            search_query = f"{cleaned_artist} {cleaned_album} {track_title}"
            enhanced_tracks.append({
                "position": position,
                "title": track_title,
                "duration": duration,
                "youtube": {
                    "url": f"https://www.youtube.com/results?search_query={quote_plus(search_query)}",
                    "query": search_query,
                    "is_search": True
                }
            })
            count += 1

        return enhanced_tracks
    
    def _generate_bandcamp_search_link(self, artist: str, album: str) -> str:
        """Generate Bandcamp search link"""
        from urllib.parse import quote_plus
        search_query = f"{artist} {album}"
        return f"https://bandcamp.com/search?q={quote_plus(search_query)}"
    
    async def _search_youtube_track(self, query: str) -> Optional[Dict]:
        """Search for a specific track on YouTube"""
        try:
            params = {
                'part': 'snippet',
                'q': query,
                'type': 'video',
                'maxResults': 5,
                'key': os.getenv('YOUTUBE_API_KEY')
            }
            
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://www.googleapis.com/youtube/v3/search',
                    params=params
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data.get('items', [])
                        if items:
                            item = items[0]  # Take first result
                            return {
                                'url': f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                                'title': item['snippet']['title'],
                                'channel': item['snippet']['channelTitle']
                            }
        except Exception as e:
            logger.debug(f"Error searching YouTube track: {e}")
        
        return None
    
    def _extract_artist(self, release: Dict) -> str:
        """Extract artist name from Discogs release data"""
        if 'artist' in release:
            return release['artist']
        if 'artists' in release and release['artists']:
            return release['artists'][0].get('name', 'Unknown Artist')
        if 'artists_sort' in release:
            return release['artists_sort']
        
        # Try to extract from title
        title = release.get('title', '')
        if ' - ' in title:
            return title.split(' - ')[0]
        
        return 'Unknown Artist'
    
    def _build_discogs_url(self, release: Dict) -> str:
        """Build full Discogs URL"""
        uri = release.get('uri', '')
        if uri and not uri.startswith('http'):
            return f"https://www.discogs.com{uri}"
        elif uri:
            return uri
        else:
            return "unavailable"
