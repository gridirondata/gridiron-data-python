"""Official Python client for the GridIron Data fantasy football API.

    from gridirondata import GridIron
    gi = GridIron()  # reads GRIDIRON_API_KEY
    print(gi.projections(7, position="QB"))
"""

__version__ = "0.1.0"

from .client import DEFAULT_BASE_URL, GridIron  # noqa: E402
from .exceptions import (  # noqa: E402
    APIError,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    GridIronConnectionError,
    GridIronError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
)

__all__ = [
    "GridIron",
    "DEFAULT_BASE_URL",
    "GridIronError",
    "ConfigurationError",
    "GridIronConnectionError",
    "APIError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "__version__",
]
