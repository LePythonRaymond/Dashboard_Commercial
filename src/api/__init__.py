"""API clients for Furious CRM."""
from .auth import FuriousAuth
from .proposals import ProposalsClient

__all__ = ["FuriousAuth", "ProposalsClient"]
