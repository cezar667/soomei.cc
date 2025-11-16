"""
Domain/custom URL management.
"""


class DomainService:
    def request_custom_domain(self, uid: str, host: str) -> None:
        raise NotImplementedError

    def approve_custom_domain(self, uid: str) -> None:
        raise NotImplementedError

