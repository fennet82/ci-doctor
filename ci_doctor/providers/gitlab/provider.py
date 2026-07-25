"""GitLab CI adapter. Translates python-gitlab objects into the domain model.

Self-hosted first: `base_url` is required (no default host), CA bundle / verify_ssl
configurable, tokens from a file or env, generous timeout. Proxies and a custom CA
also come for free from requests' env handling (HTTP(S)_PROXY, NO_PROXY,
REQUESTS_CA_BUNDLE). Feature-detection is defensive: missing attributes/endpoints
on older instances degrade to sensible defaults instead of crashing.
"""

from __future__ import annotations

import logging
from pathlib import Path

import gitlab

from ci_doctor.config.schema import Config
from ci_doctor.core.models import FailureReason, Job, MergeRequestRef, RunnerInfo, Run
from ci_doctor.core.ports import CIProvider
from ci_doctor.providers.gitlab.reasons import to_failure_reason

log = logging.getLogger("ci_doctor.gitlab")


class GitLabProvider(CIProvider):
    """Read-only GitLab adapter over python-gitlab."""

    def __init__(self, config: Config, client=None, environ: dict[str, str] | None = None):
        """Connect to GitLab, unless a client is injected.

        Args:
            config: The effective config; only `config.gitlab` is used.
            client: Pre-built `gitlab.Gitlab`, used by tests to stay offline.
            environ: Environment for the token and CI_* vars. Defaults to os.environ.

        Raises:
            ValueError: If `gitlab.base_url` is empty.
        """
        import os

        self.cfg = config.gitlab
        self.environ = os.environ if environ is None else environ
        self._project_obj = None
        self.gl = client if client is not None else self._connect()

    # --- connection -------------------------------------------------------

    def _read_token(self) -> str | None:
        """Resolve the API token.

        Returns:
            The token, or None when neither source has one — a public project
            still works unauthenticated.
        """
        # token_file (k8s/Vault secret mount) takes precedence over the env var.
        if self.cfg.token_file:
            path = Path(self.cfg.token_file)
            if path.is_file():
                return path.read_text().strip()
            log.warning("token_file %s not found; falling back to env", path)
        return self.environ.get(self.cfg.token_env)

    def _connect(self):
        """Build the python-gitlab client and probe the instance version.

        Returns:
            A configured `gitlab.Gitlab`.

        Raises:
            ValueError: If `base_url` is empty.
        """
        if not self.cfg.base_url:
            raise ValueError("gitlab.base_url must not be empty")
        ssl_verify: bool | str = self.cfg.ca_bundle or self.cfg.verify_ssl
        gl = gitlab.Gitlab(
            url=self.cfg.base_url,
            private_token=self._read_token(),
            api_version=self.cfg.api_version.lstrip("v"),  # python-gitlab wants "4"
            ssl_verify=ssl_verify,
            timeout=self.cfg.timeout_seconds,
        )
        try:
            version, revision = gl.version()
            log.info("connected to GitLab %s (%s) at %s", version, revision, self.cfg.base_url)
        except Exception as exc:  # noqa: BLE001 - version endpoint may be gated; degrade
            log.warning("could not detect GitLab version (continuing): %s", exc)
        return gl

    def _project(self):
        """Resolve and cache the project from `CI_PROJECT_ID`.

        Returns:
            The python-gitlab project object.

        Raises:
            ValueError: If `CI_PROJECT_ID` is not set.
        """
        if self._project_obj is None:
            pid = self.environ.get("CI_PROJECT_ID")
            if not pid:
                raise ValueError("CI_PROJECT_ID is not set; required to locate the pipeline")
            self._project_obj = self.gl.projects.get(pid)
        return self._project_obj

    # --- CIProvider -------------------------------------------------------

    def fetch_run(self, run_ref: str) -> Run:
        """Fetch a pipeline and map its jobs.

        Args:
            run_ref: The pipeline id.

        Returns:
            The run. Job logs are not fetched here — that happens lazily, per
            selected job, so an unread job costs nothing.
        """
        project = self._project()
        pipeline = project.pipelines.get(int(run_ref))
        jobs = [self._to_job(pj) for pj in pipeline.jobs.list(all=True)]
        log.debug("pipeline %s: %d jobs", run_ref, len(jobs))
        return Run(
            id=str(getattr(pipeline, "id", run_ref)),
            ref=getattr(pipeline, "ref", "") or "",
            sha=getattr(pipeline, "sha", "") or "",
            web_url=getattr(pipeline, "web_url", "") or "",
            mr=self._merge_request_ref(),
            jobs=jobs,
        )

    def _merge_request_ref(self) -> MergeRequestRef | None:
        """Resolve the MR from the predefined CI variable.

        Returns:
            The MR reference, or None outside an MR pipeline. Read from
            `CI_MERGE_REQUEST_IID` rather than guessed from branch or SHA.
        """
        # Resolve from the predefined CI var — reliable, no guessing from branch/sha.
        iid = self.environ.get("CI_MERGE_REQUEST_IID")
        if not iid:
            return None
        return MergeRequestRef(iid=iid, project_id=self.environ.get("CI_PROJECT_ID"))

    def fetch_job_log(self, job: Job) -> str | None:
        """Fetch one job's trace.

        Args:
            job: The job, carrying its GitLab id.

        Returns:
            The decoded trace, or None when it is missing or empty. None is valid
            data — it identifies the "never got a runner" case — so a fetch
            failure is logged and swallowed rather than raised.
        """
        # A missing/empty trace is valid data (the "never got a runner" case), not an error.
        try:
            raw = self._project().jobs.get(int(job.id)).trace()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fetch trace for job %s: %s", job.id, exc)
            return None
        if not raw:
            return None
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        log.debug("job %s trace: %d chars", job.id, len(text))
        return text or None

    def post_note(self, mr: MergeRequestRef, body: str, marker: str) -> None:
        """Post or update the report on an MR.

        Idempotent: nothing spams an MR faster than a bot that cannot find its
        own last comment.

        Args:
            mr: The merge request.
            body: The rendered Markdown report.
            marker: Hidden comment identifying our note, appended to the body.
        """
        mr_obj = self._project().mergerequests.get(int(mr.iid))
        full = f"{body}\n\n{marker}"
        for note in mr_obj.notes.list(all=True):
            if marker in (getattr(note, "body", "") or ""):
                note.body = full
                note.save()
                return
        mr_obj.notes.create({"body": full})

    # --- mapping ----------------------------------------------------------

    def _to_job(self, pj) -> Job:
        """Map a python-gitlab job onto the domain model.

        Args:
            pj: A python-gitlab job object.

        Returns:
            The neutral job. Every field is read defensively — older instances
            omit attributes, and a missing one must degrade, not crash.
        """
        status = getattr(pj, "status", "") or ""
        raw_reason = getattr(pj, "failure_reason", "") or ""
        reason = FailureReason.CANCELLED if status == "canceled" else to_failure_reason(raw_reason)
        return Job(
            id=str(pj.id),
            name=getattr(pj, "name", "") or "",
            status=status,
            stage=getattr(pj, "stage", None),
            failure_reason=reason,
            raw_failure_reason=raw_reason,
            allow_failure=bool(getattr(pj, "allow_failure", False)),
            started_at=getattr(pj, "started_at", None),
            finished_at=getattr(pj, "finished_at", None),
            duration=getattr(pj, "duration", None),
            runner=self._to_runner(getattr(pj, "runner", None)),
            needs=self._to_needs(getattr(pj, "needs", None)),
            web_url=getattr(pj, "web_url", "") or "",
            log=None,  # fetched lazily via fetch_job_log
            sections=[],
        )

    @staticmethod
    def _to_runner(runner) -> RunnerInfo | None:
        """Map GitLab's runner dict onto the domain model.

        Args:
            runner: The raw dict, or None when no runner was assigned.

        Returns:
            The runner info, or None.
        """
        if not runner:
            return None
        rid = runner.get("id")
        return RunnerInfo(
            id=str(rid) if rid is not None else None,
            description=runner.get("description"),
            tags=runner.get("tag_list") or [],
        )

    @staticmethod
    def _to_needs(needs) -> list[str]:
        """Extract upstream job names from GitLab's `needs`.

        Args:
            needs: A list of dicts or plain strings, or None.

        Returns:
            Upstream job names, used to spot cascade failures.
        """
        out = []
        for n in needs or []:
            name = n.get("name") if isinstance(n, dict) else n
            if name:
                out.append(name)
        return out
