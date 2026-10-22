from app.ai.contracts import VisionUnavailable
from app.domain.localization import VisionFeatures


class NoVision:
    """Used until the OpenAI vision extractor is configured.

    Photo localization then answers 503 AI_SERVICE_UNAVAILABLE, which the client already handles by
    offering manual location selection (API spec v0.3 §9). It never guesses what a photo shows.
    """

    def extract(self, image: bytes) -> VisionFeatures:
        raise VisionUnavailable("Photo recognition is not configured on this server.")
