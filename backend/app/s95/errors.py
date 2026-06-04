class S95Error(Exception):
    pass


class S95BanDetected(S95Error):
    pass


class S95FetchTimeout(S95Error):
    pass
