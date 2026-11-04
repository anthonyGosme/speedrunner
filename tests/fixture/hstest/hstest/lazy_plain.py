VALUE = "plain"


class Unhashable(type):
    """Its classes cannot go in a set, and comparing them raises."""

    def __eq__(cls, other):
        raise AssertionError("compared")


class Odd(metaclass=Unhashable):
    pass


KEEP = Odd()
