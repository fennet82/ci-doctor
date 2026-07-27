"""Idempotent MR-note posting against an in-memory python-gitlab double."""

from types import SimpleNamespace

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import MergeRequestRef
from ci_doctor.providers.gitlab.provider import GitLabProvider

MARKER = "<!-- ci-doctor:pipeline:99 -->"


class _Note:
    """In-memory stand-in for a python-gitlab note, recording whether it was saved."""

    def __init__(self, body):
        """Create a note with the given body."""
        self.body = body
        self.saved = False

    def save(self):
        """Record that the note was updated in place."""
        self.saved = True


class _Notes:
    """In-memory stand-in for a merge request's notes collection."""

    def __init__(self, existing):
        """Seed the collection with any pre-existing notes."""
        self._list = existing
        self.created = []

    def list(self, all=True):  # noqa: A002 - matches python-gitlab signature
        """Return every note."""
        return self._list

    def create(self, data):
        """Add a note and record it as newly created."""
        note = _Note(data["body"])
        self._list.append(note)
        self.created.append(note)
        return note


def _provider(existing_notes):
    """Build a GitLabProvider wired to fake notes, plus the notes collection."""
    notes = _Notes(existing_notes)
    mr_obj = SimpleNamespace(notes=notes)
    project = SimpleNamespace(mergerequests=SimpleNamespace(get=lambda iid: mr_obj))
    gl = SimpleNamespace(projects=SimpleNamespace(get=lambda pid: project))
    prov = GitLabProvider(load_config(environ={}), client=gl, environ={"CI_PROJECT_ID": "1"})
    return prov, notes


def test_creates_note_when_absent():
    """With no previous note, one is created carrying the marker."""
    prov, notes = _provider([])
    prov.post_note(MergeRequestRef(iid="7"), "the report", MARKER)
    assert len(notes.created) == 1
    assert MARKER in notes.created[0].body


def test_updates_existing_note_idempotently():
    """Our previous note is found by marker and edited, never duplicated."""
    existing = [_Note(f"old report\n\n{MARKER}")]
    prov, notes = _provider(existing)
    prov.post_note(MergeRequestRef(iid="7"), "new report", MARKER)
    assert notes.created == []  # updated in place, not a second comment
    assert existing[0].saved is True
    assert "new report" in existing[0].body


def test_mr_resolved_from_ci_env():
    """The MR is read from CI_MERGE_REQUEST_IID, not guessed from branch or SHA."""
    gl = SimpleNamespace(
        projects=SimpleNamespace(
            get=lambda pid: SimpleNamespace(
                pipelines=SimpleNamespace(
                    get=lambda pid: SimpleNamespace(
                        id=1, ref="mr", sha="x", web_url="", jobs=SimpleNamespace(list=lambda all=True: [])
                    )
                )
            )
        )
    )
    prov = GitLabProvider(
        load_config(environ={}), client=gl, environ={"CI_PROJECT_ID": "1", "CI_MERGE_REQUEST_IID": "42"}
    )
    run = prov.fetch_run("1")
    assert run.mr is not None and run.mr.iid == "42"
