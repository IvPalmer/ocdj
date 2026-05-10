"""
Handles visual search using Google Cloud Vision API.

V1 ocdj: import the vision SDK lazily because (a) the heavy `google-cloud-vision`
package isn't in V1 requirements (Gemini Vision covers the happy path) and
(b) the real client needs a service-account JSON via GCP_SA. If neither the
package nor the credentials are available, mark the collector unconfigured —
hybrid_search already gracefully skips it on exception.
"""
import logging
import os

logger = logging.getLogger(__name__)


class VisionCollector:
    """
    Uses Google Vision API's Web Detection feature to identify album covers.
    """
    def __init__(self, name="vision"):
        self.name = name
        self.client = None
        self.configured = False

        sa_json = os.getenv('CRATEMATE_GCP_SA_JSON', '')
        if not sa_json or sa_json == '__PENDING__':
            logger.warning(
                "VisionCollector not configured — CRATEMATE_GCP_SA_JSON missing. "
                "OCR fallback disabled; hybrid search will rely on Gemini only."
            )
            return

        try:
            from google.cloud import vision  # type: ignore  # lazy
            self.client = vision.ImageAnnotatorClient()
            self.configured = True
        except ImportError:
            logger.warning(
                "google-cloud-vision not installed — VisionCollector disabled. "
                "Install if OCR fallback is needed in V2."
            )
        except Exception as e:
            logger.warning("VisionCollector init failed: %s", e)

    async def identify_album_cover(self, image_bytes: bytes) -> dict:
        """
        Identifies an album cover using Google Vision API Web Detection.

        Args:
            image_bytes: The byte content of the image.

        Returns:
            A dictionary containing the best guess for the album's title and artist,
            or an error message if identification fails.
        """
        try:
            image = vision.Image(content=image_bytes)
            response = self.client.web_detection(image=image)
            annotations = response.web_detection

            best_guess = None
            if annotations.best_guess_labels:
                best_guess = annotations.best_guess_labels[0].label
                logger.info(f"Vision API best guess: {best_guess}")

            # Gather additional clues: high-score web entities and page titles
            entities = []
            if annotations.web_entities:
                for entity in annotations.web_entities:
                    if getattr(entity, 'description', None):
                        # Keep top entities regardless of containing the words 'album' or 'artist'
                        entities.append(entity.description)

            titles = []
            if annotations.pages_with_matching_images:
                for page in annotations.pages_with_matching_images[:5]:
                    title = getattr(page, 'page_title', None)
                    if title:
                        titles.append(title)

            if best_guess or entities or titles:
                return {
                    "success": True,
                    "best_guess": best_guess or "",
                    "entities": entities[:10],
                    "web_titles": titles[:5]
                }

            return {"success": False, "error": "No confident guess from Vision API."}

        except Exception as e:
            logger.error(f"Error calling Google Vision API for web detection: {str(e)}")
            return {"success": False, "error": str(e)}

    async def extract_text_from_image(self, image_bytes: bytes) -> dict:
        """
        Extracts text from an image using Google Vision API OCR.

        Args:
            image_bytes: The byte content of the image.

        Returns:
            A dictionary containing the extracted text lines, or an error message.
        """
        try:
            image = vision.Image(content=image_bytes)
            response = self.client.text_detection(image=image)
            texts = response.text_annotations

            cleaned_lines = []
            if texts:
                full_text = texts[0].description
                lines = full_text.split('\n')
                cleaned_lines = [line.strip() for line in lines if line.strip()]

            # If basic OCR found nothing, try document_text_detection which can be better for angled text
            if not cleaned_lines:
                try:
                    doc_response = self.client.document_text_detection(image=image)
                    doc_text = getattr(doc_response, 'full_text_annotation', None)
                    if doc_text and getattr(doc_text, 'text', None):
                        lines = [l.strip() for l in doc_text.text.split('\n') if l.strip()]
                        cleaned_lines = lines
                except Exception:
                    pass

            if cleaned_lines:
                logger.info(f"Vision API OCR extracted text: {cleaned_lines}")
                return {"success": True, "text_lines": cleaned_lines}

            return {"success": False, "error": "No text found by Vision API OCR."}

        except Exception as e:
            logger.error(f"Error calling Google Vision API for OCR: {str(e)}")
            return {"success": False, "error": str(e)}

    def get_name(self):
        return self.name
