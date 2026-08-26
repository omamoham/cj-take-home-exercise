"""
Unit tests for reconciler.py's core logic.

No kopf, no real or mocked Kubernetes API server -- just FakePodsAPI, a
plain in-memory dict standing in for a real cluster. Because
reconcile() only ever talks to the PodsAPI Protocol (never to kopf or
the kubernetes library directly), we can hand it this fake and get
identical behavior to the real thing, at a tiny fraction of the speed
cost -- the whole suite runs in milliseconds, not the literal minutes
it would take to test "waits 3 minutes" against a real clock.
"""
from datetime import datetime, timedelta, timezone

import pytest

from cjpod_operator.reconciler import PodInfo, PodsAPI, POD_LIFETIME, reconcile

# Shared test fixtures -- same CjPod identity and Pod template reused
# across every test below, so each test only needs to set up the ONE
# thing it's actually testing (how old the Pod is, who owns it, etc.)
CJPOD_UID = "cjpod-uid-123"
NAME = "cjpod-123"
NAMESPACE = "default"
TEMPLATE = {"spec": {"containers": [{"name": "abc", "image": "nginx"}]}}


class FakePodsAPI:
    """
    A hand-written stand-in for KubernetesPodsAPI, good enough to
    satisfy the PodsAPI Protocol (Python checks the shape, not the
    inheritance -- this class never needs to say `(PodsAPI)` after its
    name for reconcile() to accept it). Pods live in a plain dict keyed
    by (namespace, name), and every delete() call is recorded in
    `self.deleted` so tests can assert on exactly what got removed.
    """

    def __init__(self):
        self.pods: dict[tuple[str, str], PodInfo] = {}
        self.deleted: list[str] = []

    def get(self, name, namespace):
        return self.pods.get((namespace, name))

    def create(self, name, namespace, pod_spec, owner):
        info = PodInfo(
            name=name,
            namespace=namespace,
            uid=f"pod-uid-{name}",
            creation_timestamp=datetime.now(timezone.utc),
            owner_uid=owner.uid,
            owner_controller=True,
        )
        self.pods[(namespace, name)] = info
        return info

    def delete(self, name, namespace, uid):
        # Mirrors the real implementation's UID precondition: only
        # actually remove the Pod if the UID still matches what the
        # caller expected to be deleting.
        key = (namespace, name)
        existing = self.pods.get(key)
        if existing is not None and existing.uid == uid:
            del self.pods[key]
            self.deleted.append(name)


def seeded_pod(created_at, owner_uid=CJPOD_UID, controller=True):
    """Builds a PodInfo directly -- i.e. without going through
    reconcile()'s create path -- so each test can start from "a Pod
    that's already N minutes old" instead of having to first create one
    and then somehow fast-forward time to age it."""
    return PodInfo(
        name=NAME,
        namespace=NAMESPACE,
        uid=f"pod-uid-{NAME}",
        creation_timestamp=created_at,
        owner_uid=owner_uid,
        owner_controller=controller,
    )


def test_creates_pod_when_missing():
    """No Pod exists yet -> reconcile() should create one and ask to
    be checked again in exactly POD_LIFETIME (the Pod is brand new, so
    its full window is still ahead)."""
    pods = FakePodsAPI()

    result = reconcile(
        cjpod_name=NAME, cjpod_namespace=NAMESPACE, cjpod_uid=CJPOD_UID,
        pod_template=TEMPLATE, already_deleted=False, pods=pods,
        now=datetime.now(timezone.utc),
    )

    assert result.phase == "Running"
    # pytest.approx allows tiny floating-point differences instead of
    # requiring an exact match -- appropriate here since
    # POD_LIFETIME.total_seconds() and the value inside reconcile() are
    # computed independently, even though they should agree exactly.
    assert result.requeue_after_seconds == pytest.approx(POD_LIFETIME.total_seconds())
    assert pods.get(NAME, NAMESPACE) is not None


def test_keeps_running_before_deadline():
    """A Pod that's only 1 minute old -> should NOT be deleted, and the
    remaining time reported back should be the leftover ~2 minutes,
    not the full 3 minutes again."""
    now = datetime.now(timezone.utc)
    pods = FakePodsAPI()
    pods.pods[(NAMESPACE, NAME)] = seeded_pod(now - timedelta(minutes=1))

    result = reconcile(
        cjpod_name=NAME, cjpod_namespace=NAMESPACE, cjpod_uid=CJPOD_UID,
        pod_template=TEMPLATE, already_deleted=False, pods=pods, now=now,
    )

    assert result.phase == "Running"
    assert pods.get(NAME, NAMESPACE) is not None  # still there
    # abs=1 allows +/- 1 second of slack for the tiny amount of real
    # time that elapses between building `now` above and reconcile()
    # doing its own subtraction internally.
    assert result.requeue_after_seconds == pytest.approx(120, abs=1)


def test_deletes_pod_after_deadline():
    """A Pod just past its 3-minute mark -> should be deleted, and the
    CjPod's status should reflect that."""
    now = datetime.now(timezone.utc)
    pods = FakePodsAPI()
    pods.pods[(NAMESPACE, NAME)] = seeded_pod(now - timedelta(minutes=3, seconds=10))

    result = reconcile(
        cjpod_name=NAME, cjpod_namespace=NAMESPACE, cjpod_uid=CJPOD_UID,
        pod_template=TEMPLATE, already_deleted=False, pods=pods, now=now,
    )

    assert result.phase == "Deleted"
    assert pods.get(NAME, NAMESPACE) is None       # actually gone
    assert pods.deleted == [NAME]                   # and it was OUR delete() call that removed it


def test_recovers_after_restart():
    """
    The test that most directly proves the restart-recovery
    requirement. A Pod 10 minutes overdue -- as if the operator had
    been completely down for 7+ minutes past when it should have
    deleted this Pod -- reconciled by a call with absolutely no memory
    of anything that happened before (a fresh FakePodsAPI, no prior
    reconcile() calls in this test at all). This is exactly what the
    FIRST reconcile after a real operator restart looks like.
    """
    now = datetime.now(timezone.utc)
    pods = FakePodsAPI()
    pods.pods[(NAMESPACE, NAME)] = seeded_pod(now - timedelta(minutes=10))

    result = reconcile(
        cjpod_name=NAME, cjpod_namespace=NAMESPACE, cjpod_uid=CJPOD_UID,
        pod_template=TEMPLATE, already_deleted=False, pods=pods, now=now,
    )

    # A single reconcile() call, with zero prior state, still gets the
    # right answer -- because the deadline came from the Pod's own
    # creation_timestamp, not from anything this function remembered.
    assert result.phase == "Deleted"
    assert pods.get(NAME, NAMESPACE) is None


def test_does_not_touch_unowned_pod():
    """A Pod with the right name but a DIFFERENT owner UID -- simulating
    a user who created a same-named Pod by hand before this CjPod was
    ever reconciled. Even though it's 10 minutes old (well past the
    deadline, if it were ours), it must be left completely alone."""
    now = datetime.now(timezone.utc)
    pods = FakePodsAPI()
    pods.pods[(NAMESPACE, NAME)] = seeded_pod(
        now - timedelta(minutes=10), owner_uid="someone-elses-uid", controller=True
    )

    result = reconcile(
        cjpod_name=NAME, cjpod_namespace=NAMESPACE, cjpod_uid=CJPOD_UID,
        pod_template=TEMPLATE, already_deleted=False, pods=pods, now=now,
    )

    assert result.phase == "Error"
    assert pods.get(NAME, NAMESPACE) is not None  # still there -- untouched
    assert pods.deleted == []                      # delete() was never called


def test_no_recreate_after_completed_lifecycle():
    """Once a CjPod's status already says Deleted, reconcile() must
    NEVER create a new Pod for it again, even though pods.get() will
    (correctly) return None -- the same "no Pod" state a brand-new
    CjPod would also start in. already_deleted=True is what tells
    reconcile() to tell these two situations apart."""
    pods = FakePodsAPI()

    result = reconcile(
        cjpod_name=NAME, cjpod_namespace=NAMESPACE, cjpod_uid=CJPOD_UID,
        pod_template=TEMPLATE, already_deleted=True, pods=pods,
        now=datetime.now(timezone.utc),
    )

    assert result.phase == "Deleted"
    assert pods.get(NAME, NAMESPACE) is None  # confirms no Pod was created
