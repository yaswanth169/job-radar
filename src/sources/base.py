"""Every company source implements this."""

from __future__ import annotations

from typing import List, Protocol

from src.models import CompanyConfig, Job


class Source(Protocol):
    def fetch(self) -> List[Job]:
        """Return every job currently matching this company's filters."""
        ...

    def enrich(self, job: Job) -> Job:
        """Fill in the job's full description, if the source has one."""
        ...


class BaseSource:
    def __init__(self, config: CompanyConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    def fetch(self) -> List[Job]:
        raise NotImplementedError

    def enrich(self, job: Job) -> Job:
        """Load the job's full description. Default: nothing to add.

        Called only for jobs we have never seen, so a routine poll stays
        at a single request.
        """
        return job
