class PixivException(Exception):
    """Base class for all Pixiv errors"""

    pass


class LoginError(PixivException):
    """Login Pixiv failed with error"""

    pass


class GetIllustrationError(PixivException):
    """Get illust information failed"""

    pass


class DownloadError(PixivException):
    """Download illustration failed"""

    pass


class SearchError(PixivException):
    """Searching for image failed"""

    pass


class SearchRelatedError(SearchError):
    """Searching for related image failed"""

    pass


class GetUgoiraError(PixivException):
    """Get an ugoira failed"""

    pass


class NotAnUgoiraError(GetUgoiraError):
    """The illust trying to get is not an ugoira"""

    pass
