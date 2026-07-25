"""GitHub Actions adapter. Maps the REST API's workflow-run/jobs into the domain
model — the same domain model GitLab produces, so nothing downstream changes.

The GitHub API is *better* structured than GitLab's here (per-step conclusions),
but the log-based path already works, so this stays a thin mapping. Self-hosted
(GHE) is supported via a configurable base_url; token from file or env; custom CA
and env proxies honoured by requests.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ci_doctor.config.schema import Config, GitHubConfig
from ci_doctor.core.models import Job, MergeRequestRef, RunnerInfo, Run
from ci_doctor.core.ports import CIProvider
from ci_doctor.providers.github.reasons import to_failure_reason

log = logging.getLogger("ci_doctor.github")

#: Conclusions that mean "this job failed". Mapped onto the domain's single
#: "failed" status so core job-selection needs no GitHub knowledge.
_FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure", "cancelled"}


def _read_token(cfg: GitHubConfig, environ: dict[str, str]) -> str | None:
    """Resolve the API token, file first.

    Args:
        cfg: GitHub config supplying `token_file` and `token_env`.
        environ: Environment to read `token_env` from.

    Returns:
        The token, or None when neither source has one.
    """
    if cfg.token_file:
        path = Path(cfg.token_file)
        if path.is_file():
            return path.read_text().strip()
    return environ.get(cfg.token_env)


class GitHubApi:
    """Minimal REST client (requests). Injected fakes replace it in tests."""

    def __init__(self, cfg: GitHubConfig, environ: dict[str, str]):
        """Capture connection settings.

        Args:
            cfg: GitHub config — base URL, token, CA bundle, timeout.
            environ: Environment to read the token from.

        Raises:
            ValueError: If `github.base_url` is empty.
        """
        if not cfg.base_url:
            raise ValueError("github.base_url must not be empty")
        self.base = cfg.base_url.rstrip("/")
        self.token = _read_token(cfg, environ)
        self.verify = cfg.ca_bundle or cfg.verify_ssl
        self.timeout = cfg.timeout_seconds

    def _headers(self):
        """Build request headers.

        Returns:
            Accept plus, when a token is available, Authorization. Anonymous
            requests still work against public repos.
        """
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, path):
        """Issue a GET against the API.

        Args:
            path: Path below the base URL, e.g. "/repos/o/r/actions/runs/1/jobs".

        Returns:
            The raw `requests.Response`; callers decide how to treat the status.
        """
        import requests

        return requests.get(f"{self.base}{path}", headers=self._headers(), verify=self.verify, timeout=self.timeout)

    def run_jobs(self, repo, run_id) -> list[dict]:
        """List a workflow run's jobs.

        Args:
            repo: "owner/name".
            run_id: The workflow run id.

        Returns:
            Raw job dicts.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        r = self._get(f"/repos/{repo}/actions/runs/{run_id}/jobs")
        r.raise_for_status()
        return r.json().get("jobs", [])

    def job_log(self, repo, job_id) -> str:
        """Download one job's log.

        Args:
            repo: "owner/name".
            job_id: The job id.

        Returns:
            The log text, or "" when GitHub has none (404 — logs expire, and a
            job that never started never wrote any).

        Raises:
            requests.HTTPError: On a non-2xx response other than 404.
        """
        r = self._get(f"/repos/{repo}/actions/jobs/{job_id}/logs")
        if r.status_code == 404:
            return ""
        r.raise_for_status()
        return r.text

    def list_comments(self, repo, pr) -> list[dict]:
        """List a pull request's comments.

        Args:
            repo: "owner/name".
            pr: The pull request number.

        Returns:
            Raw comment dicts.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        r = self._get(f"/repos/{repo}/issues/{pr}/comments")
        r.raise_for_status()
        return r.json()

    def create_comment(self, repo, pr, body) -> None:
        """Add a comment to a pull request.

        Args:
            repo: "owner/name".
            pr: The pull request number.
            body: Markdown body.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        import requests

        requests.post(f"{self.base}/repos/{repo}/issues/{pr}/comments", headers=self._headers(),
                      json={"body": body}, verify=self.verify, timeout=self.timeout).raise_for_status()

    def update_comment(self, repo, comment_id, body) -> None:
        """Replace an existing comment's body.

        Args:
            repo: "owner/name".
            comment_id: The comment id.
            body: New Markdown body.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        import requests

        requests.patch(f"{self.base}/repos/{repo}/issues/comments/{comment_id}", headers=self._headers(),
                       json={"body": body}, verify=self.verify, timeout=self.timeout).raise_for_status()


class GitHubProvider(CIProvider):
    """Read-only GitHub Actions adapter."""

    def __init__(self, config: Config, client=None, environ: dict[str, str] | None = None):
        """Prepare the API client.

        Args:
            config: The effective config; only `config.github` is used.
            client: Pre-built API client, used by tests to stay offline.
            environ: Environment for the token and GITHUB_* vars.
        """
        import os

        self.cfg = config.github
        self.environ = os.environ if environ is None else environ
        self.client = client if client is not None else GitHubApi(self.cfg, self.environ)

    def _repo(self) -> str:
        """Resolve the repository from the environment.

        Returns:
            "owner/name".

        Raises:
            ValueError: If `GITHUB_REPOSITORY` is not set.
        """
        repo = self.environ.get("GITHUB_REPOSITORY")
        if not repo:
            raise ValueError("GITHUB_REPOSITORY is not set; required to locate the run")
        return repo

    def fetch_run(self, run_ref: str) -> Run:
        """Fetch a workflow run and map its jobs.

        Args:
            run_ref: The workflow run id.

        Returns:
            The run. Job logs are fetched lazily, per selected job.
        """
        jobs = [self._to_job(j) for j in self.client.run_jobs(self._repo(), run_ref)]
        log.debug("run %s: %d jobs", run_ref, len(jobs))
        return Run(
            id=str(run_ref),
            ref=self.environ.get("GITHUB_REF", ""),
            sha=self.environ.get("GITHUB_SHA", ""),
            web_url="",
            mr=self._pr_ref(),
            jobs=jobs,
        )

    def fetch_job_log(self, job: Job) -> str | None:
        """Fetch one job's log.

        Args:
            job: The job, carrying its GitHub id.

        Returns:
            The log, or None when empty or missing — valid data, not an error.
        """
        raw = self.client.job_log(self._repo(), job.id)
        return raw or None  # empty/missing log is valid data

    def post_note(self, mr: MergeRequestRef, body: str, marker: str) -> None:
        """Post or update the report on a pull request.

        Idempotent: finds our previous comment by `marker` and edits it rather
        than adding a new one on every run.

        Args:
            mr: The pull request.
            body: The rendered Markdown report.
            marker: Hidden comment identifying our note, appended to the body.
        """
        full = f"{body}\n\n{marker}"
        for comment in self.client.list_comments(self._repo(), mr.iid):
            if marker in (comment.get("body", "") or ""):
                self.client.update_comment(self._repo(), comment["id"], full)
                return
        self.client.create_comment(self._repo(), mr.iid, full)

    def _pr_ref(self) -> MergeRequestRef | None:
        """Resolve the pull request from `GITHUB_REF`.

        Returns:
            The PR reference, or None when the run is not for a PR.
        """
        m = re.match(r"refs/pull/(\d+)/", self.environ.get("GITHUB_REF", ""))
        return MergeRequestRef(iid=m.group(1)) if m else None

    def _to_job(self, j: dict) -> Job:
        """Map a GitHub job dict onto the domain model.

        Args:
            j: The raw job dict.

        Returns:
            The neutral job, with its conclusion normalised to the domain's
            "failed" status.
        """
        conclusion = j.get("conclusion")
        startup = j.get("status") == "startup_failure" or conclusion == "startup_failure"
        # Normalize to the domain's "failed" so core job-selection needs no GitHub knowledge.
        status = "failed" if conclusion in _FAILED_CONCLUSIONS else (j.get("status") or "")
        runner = RunnerInfo(description=j.get("runner_name")) if j.get("runner_name") else None
        return Job(
            id=str(j["id"]),
            name=j.get("name", "") or "",
            status=status,
            stage=None,
            failure_reason=to_failure_reason(conclusion, startup_failure=startup),
            raw_failure_reason=conclusion or "",
            allow_failure=False,
            started_at=j.get("started_at"),
            finished_at=j.get("completed_at"),
            runner=runner,
            needs=[],
            web_url=j.get("html_url", "") or "",
            log=None,
            sections=[],
        )
