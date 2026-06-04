class ParkrunFetchError(Exception):
    pass


class ParkrunFetchTimeout(ParkrunFetchError):
    pass


class ParkrunBanDetected(ParkrunFetchError):
    pass


class ParkrunProfileNotFound(ParkrunFetchError):
    pass


class ParkrunProfileParseError(ParkrunFetchError):
    pass
