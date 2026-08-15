"""GitHub Actions adapter. Translates PyGithub objects into the domain model —
the same domain model GitLab produces, so nothing downstream changes.

Self-hosted (GHE) is supported via a configurable base_url; token from file or
env; custom CA and env proxies honoured by requests underneath PyGithub. The one
call PyGithub cannot finish for us is the job log: it hands back the redirect
target for the log blob, and we fetch that ourselves.
"""

import logging
import re
from collections.abc import Mapping
from typing import Any

from github import Auth, Github, GithubException

from ci_doctor.config.schema import Config
from ci_doctor.core.models import Job, MergeRequestRef, RunnerInfo, Run
from ci_doctor.core.ports import CIProvider, SCMProvider
from ci_doctor.providers.git_origin import origin_repo
from ci_doctor.providers.github.reasons import to_failure_reason
from ci_doctor.providers.tokens import read_token

log = logging.getLogger("ci_doctor.github")

#: Conclusions that mean "this job failed". Mapped onto the domain's single
#: "failed" status so core job-selection needs no GitHub knowledge.
_FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure", "cancelled"}


class GitHubProvider(CIProvider, SCMProvider):
    """Read-only GitHub Actions adapter over PyGithub.

    Serves both roles: workflow runs and pull requests are the same API behind
    the same client, so one instance answers as CI system and as git host.
    """

    def __init__(self, config: Config, client=None, environ: Mapping[str, str] | None = None):
        """Connect to GitHub, unless a client is injected.

        Args:
            config: The effective config; only `config.github` is used.
            client: Pre-built `github.Github`, used by tests to stay offline.
            environ: Environment for the token and GITHUB_* vars. Defaults to os.environ.
        """
        import os

        self.cfg = config.github
        self.environ = os.environ if environ is None else environ
        self.verify: bool | str = self.cfg.ca_bundle or self.cfg.verify_ssl
        self._repo_obj = None
        #: Raw PyGithub jobs kept from fetch_run, keyed by id: the log lives behind
        #: a method on the job object and the API exposes no by-id job lookup.
        self._raw_jobs: dict[str, Any] = {}
        self.gh = client if client is not None else self._connect()

    # --- connection -------------------------------------------------------

    def _connect(self) -> Github:
        """Build the PyGithub client.

        Returns:
            A configured `github.Github`.

        Raises:
            ValueError: If `github.base_url` is empty.
        """
        if not self.cfg.base_url:
            raise ValueError("github.base_url must not be empty")
        token = read_token(self.cfg.token_file, self.cfg.token_env, self.environ)
        return Github(
            auth=Auth.Token(token) if token else None,
            base_url=self.cfg.base_url.rstrip("/"),
            verify=self.verify,
            timeout=self.cfg.timeout_seconds,
        )

    def _repo(self):
        """Resolve and cache the repository.

        Returns:
            The PyGithub repository object.

        Raises:
            ValueError: If the repository is set neither in the environment nor
                by a git origin to fall back on.
        """
        if self._repo_obj is None:
            name = self.environ.get("GITHUB_REPOSITORY") or origin_repo("GITHUB_REPOSITORY")
            if not name:
                raise ValueError("GITHUB_REPOSITORY is not set and git origin gave no repository")
            self._repo_obj = self.gh.get_repo(name)
        return self._repo_obj

    # --- CIProvider -------------------------------------------------------

    def fetch_run(self, run_ref: str) -> Run:
        """Fetch a workflow run and map its jobs.

        Args:
            run_ref: The workflow run id.

        Returns:
            The run. Job logs are not fetched here — that happens lazily, per
            selected job, so an unread job costs nothing.
        """
        wr = self._repo().get_workflow_run(int(run_ref))
        self._raw_jobs = {str(j.id): j for j in wr.jobs()}
        jobs = [self._to_job(j) for j in self._raw_jobs.values()]
        log.debug("run %s: %d jobs", run_ref, len(jobs))
        return Run(
            id=str(wr.id),
            ref=wr.head_branch or "",
            sha=wr.head_sha or "",
            web_url=wr.html_url or "",
            mr=self._pr_ref(wr),
            jobs=jobs,
        )

    def fetch_job_log(self, job: Job) -> str | None:
        """Fetch one job's log.

        Args:
            job: The job, carrying its GitHub id.

        Returns:
            The log, or None when empty or missing. None is valid data — it
            identifies the "never got a runner" case — so a fetch failure is
            logged and swallowed rather than raised.
        """
        import requests

        raw = self._raw_jobs.get(job.id)
        if raw is None:
            log.warning("no raw job cached for %s; cannot fetch its log", job.id)
            return None
        try:
            # Logs expire, and a job that never started never wrote any: 404 here.
            url = raw.logs_url()
            resp = requests.get(url, verify=self.verify, timeout=self.cfg.timeout_seconds)
            # The redirect target is a short-lived signed URL; an expired one answers
            # with an XML error body, which would otherwise be analyzed as the log.
            resp.raise_for_status()
            text = resp.text
        except (GithubException, requests.RequestException) as exc:
            log.warning("could not fetch log for job %s: %s", job.id, exc)
            return None
        log.debug("job %s log: %d chars", job.id, len(text))
        return text or None

    # --- SCMProvider ------------------------------------------------------

    def post_note(self, mr: MergeRequestRef, body: str, marker: str) -> None:
        """Post or update the report on a pull request.

        Idempotent: finds our previous comment by `marker` and edits it rather
        than adding a new one on every run.

        Args:
            mr: The pull request.
            body: The rendered Markdown report.
            marker: Hidden comment identifying our note, appended to the body.
        """
        pr = self._repo().get_pull(int(mr.iid))
        full = f"{body}\n\n{marker}"
        for comment in pr.get_issue_comments():
            if marker in (comment.body or ""):
                comment.edit(full)
                return
        pr.create_issue_comment(full)

    # --- mapping ----------------------------------------------------------

    def _pr_ref(self, wr) -> MergeRequestRef | None:
        """Resolve the pull request for a run.

        Args:
            wr: The PyGithub workflow run.

        Returns:
            The PR reference, or None when the run is not for a PR. The run's own
            `pull_requests` is authoritative; `GITHUB_REF` is the fallback for
            fork PRs, where GitHub leaves that list empty.
        """
        for pr in wr.pull_requests or []:
            return MergeRequestRef(iid=str(pr.number))
        m = re.match(r"refs/pull/(\d+)/", self.environ.get("GITHUB_REF", ""))
        return MergeRequestRef(iid=m.group(1)) if m else None

    def _to_job(self, j) -> Job:
        """Map a PyGithub workflow job onto the domain model.

        Args:
            j: A PyGithub `WorkflowJob`.

        Returns:
            The neutral job, with its conclusion normalised to the domain's
            "failed" status.
        """
        conclusion = j.conclusion
        startup = j.status == "startup_failure" or conclusion == "startup_failure"
        # Normalize to the domain's "failed" so core job-selection needs no GitHub knowledge.
        status = "failed" if conclusion in _FAILED_CONCLUSIONS else (j.status or "")
        runner = (
            RunnerInfo(id=_str_or_none(j.runner_id), description=j.runner_name) if j.runner_name else None
        )
        return Job(
            id=str(j.id),
            name=j.name or "",
            status=status,
            stage=None,
            failure_reason=to_failure_reason(conclusion, startup_failure=startup),
            raw_failure_reason=conclusion or "",
            allow_failure=False,
            started_at=_iso(j.started_at),
            finished_at=_iso(j.completed_at),
            runner=runner,
            needs=[],
            web_url=j.html_url or "",
            log=None,  # fetched lazily via fetch_job_log
            sections=[],
        )


def _str_or_none(value) -> str | None:
    """Stringify an optional id without turning None into "None"."""
    return None if value is None else str(value)


def _iso(value) -> str | None:
    """Normalise PyGithub's `datetime` timestamps to the domain's ISO strings.

    The domain model is a plain dataclass, so nothing downstream would catch a
    `datetime` slipping through — it would surface as a JSON dump crash instead.
    """
    return value.isoformat() if hasattr(value, "isoformat") else value or None
