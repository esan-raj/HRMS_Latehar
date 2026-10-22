class DomainError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class FieldError(DomainError):
    def __init__(self, field: str, message: str):
        super().__init__(message, 400)
        self.field = field


class StaleFormError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "The employee form changed. Reload this page and review the fields before saving.",
            409,
        )


class SelectionError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 400)


class StaleEditError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "This record was saved somewhere else. The form now shows the latest values. Review them and save again.",
            409,
        )
