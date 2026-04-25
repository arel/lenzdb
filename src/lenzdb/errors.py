"""Project-specific exceptions."""


class LenzError(Exception):
    """Base error for LenzDB."""


class ProjectError(LenzError):
    """Raised when project configuration or source data is invalid."""


class LensAnalysisError(LenzError):
    """Raised when a lens cannot be analyzed safely."""


class MutationError(LenzError):
    """Raised when an edit cannot be planned or applied safely."""
