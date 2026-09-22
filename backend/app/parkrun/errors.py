class ParkrunFetchError(Exception):
    pass


class ParkrunFetchTimeout(ParkrunFetchError):
    pass


class ParkrunBanDetected(ParkrunFetchError):
    pass


class ParkrunExitUnavailable(ParkrunFetchError):
    """Ни один исходящий выход не ответил — сеть наша, а не parkrun.

    Отличать от ParkrunBanDetected принципиально: там нас узнали и не пустили
    (лестница охлаждения, капча), а здесь мёртв туннель VPN — parkrun об этом
    запросе даже не слышал. Поднимать по такому поводу кулдаун и жечь попытки
    строки очереди не за что.
    """


class ParkrunProfileNotFound(ParkrunFetchError):
    pass


class ParkrunProfileParseError(ParkrunFetchError):
    pass
