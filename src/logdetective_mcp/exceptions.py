class DecompressionError(Exception):
    """Raised when decompression fails or safety limits are exceeded."""


class DownloadError(Exception):
    """Raised on failed download"""
