"""As pyparsing's: its __del__ has nothing to do, so a grammar built at import
must not keep the module out of the daemon."""


class Forward:
    def __del__(self):
        pass


GRAMMAR = Forward()
