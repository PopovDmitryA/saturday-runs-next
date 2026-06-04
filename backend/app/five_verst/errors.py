class FiveVerstError(Exception):
    pass


class FiveVerstBanDetected(FiveVerstError):
    pass


class FiveVerstFetchTimeout(FiveVerstError):
    pass
