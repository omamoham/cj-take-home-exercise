"""
Core CjPod reconciliation logic.

This module is deliberately kept independent of kopf's runtime AND of
the real Kubernetes client library -- it never imports `kopf` or
`kubernetes`, and it never calls datetime.now() directly. Everything
it needs (the current time, a way to talk to Pods) is passed in as an
argument instead. That's what makes it possible to unit test this
file's entire logic in milliseconds, with no real cluster and no
waiting around for 3 real minutes to pass -- see tests/test_reconciler.py.
"""
from __future__ import annotations  # lets us use "PodInfo" as a type hint before it's fully defined below

import dataclasses
from datetime import datetime, timedelta
from typing import Optional, Protocol

# The one constant that defines this whole operator's behavior. Change
# it here, nowhere else -- every place that needs "how long a Pod
# should live" reads from this single source.
POD_LIFETIME = timedelta(minutes=3)


@dataclasses.dataclass
class OwnerInfo:
    """
    A @dataclass is a plain Python class where you just declare the
    fields and Python auto-generates __init__, __repr__, and __eq__
    for you -- no boilerplate. This one carries just enough information
    to build a Kubernetes "ownerReference": a pointer from the Pod back
    to the CjPod that created it.
    """
    api_version: str
    kind: str
    name: str
    uid: str  # Kubernetes' globally-unique ID for the CjPod object -- NOT its name (names can collide across time; UIDs never do)


@dataclasses.dataclass
class PodInfo:
    """
    A plain-data snapshot of "the parts of a Pod this operator actually
    cares about" -- not a real Kubernetes Pod object. Keeping this
    separate from whatever the real `kubernetes` client library returns
    is what lets reconciler.py stay ignorant of that library entirely;
    k8s_pods.py is the only place that translates a real Pod into one
    of these.
    """
    name: str
    namespace: str
    uid: str
    creation_timestamp: datetime  # set once by the Kubernetes API server, never changes -- this is our restart-proof clock
    owner_uid: Optional[str]      # UID of whichever object "controls" this Pod, if any
    owner_controller: bool        # True only if that ownership is a controller reference (see is_owned_by below)


@dataclasses.dataclass
class ReconcileResult:
    """What reconcile() hands back to its caller: what phase the CjPod
    is now in, and (optionally) how many seconds to wait before
    checking on it again."""
    phase: str  # "Running" | "Deleted" | "Error"
    message: str = ""
    requeue_after_seconds: Optional[float] = None  # None = nothing further to schedule


class PodsAPI(Protocol):
    """
    A Protocol (PEP 544) describes a *shape* -- any object with these
    three methods satisfies this type, whether or not it explicitly
    inherits from PodsAPI. This is Python's version of an interface:
    the real implementation (KubernetesPodsAPI in k8s_pods.py) talks to
    an actual cluster; the test implementation (FakePodsAPI in the test
    file) is just an in-memory dict. reconcile() below doesn't care
    which one it's given, which is exactly the point -- it's how the
    tests avoid needing a real cluster at all.
    """

    def get(self, name: str, namespace: str) -> Optional[PodInfo]: ...
    def create(self, name: str, namespace: str, pod_spec: dict, owner: OwnerInfo) -> PodInfo: ...
    def delete(self, name: str, namespace: str, uid: str) -> None: ...


def is_owned_by(pod: PodInfo, owner_uid: str) -> bool:
    """
    Mirrors Kubernetes' own notion of "controlled by": true only if the
    Pod carries a controller=true owner reference whose UID matches the
    CjPod we're reconciling. A NAME match is never enough on its own --
    someone could have created an unrelated Pod by hand with the exact
    same name before this CjPod was ever reconciled, and we must never
    touch that Pod. See test_does_not_touch_unowned_pod for the case
    this guards against.
    """
    return pod.owner_controller and pod.owner_uid == owner_uid


def reconcile(
    *,  # everything after this must be passed as a keyword argument (name=value) -- prevents accidentally swapping two positional args of the same type, like two strings
    cjpod_name: str,
    cjpod_namespace: str,
    cjpod_uid: str,
    pod_template: dict,
    already_deleted: bool,
    pods: PodsAPI,
    now: datetime,
) -> ReconcileResult:
    """
    The entire operator's decision-making logic, in one function.

    Call this from kopf's real runtime, or from a unit test with a
    fake `pods` and a hand-picked `now` -- it behaves identically
    either way, because it never reaches outside its own arguments for
    anything. That single property is also what makes it safe across
    operator restarts: a freshly restarted process calling this
    function has no memory of anything that happened before, and it
    doesn't need any -- everything it needs to make the right decision
    is either passed in fresh (now) or read fresh from Kubernetes
    itself (whatever pods.get() returns, including the Pod's real
    creation_timestamp).
    """

    # Step 1: does a Pod for this CjPod already exist?
    existing = pods.get(cjpod_name, cjpod_namespace)

    if existing is None:
        # No Pod right now. Two different reasons that could be true:
        if already_deleted:
            # We already finished this CjPod's lifecycle in an earlier
            # reconcile (it ran its 3 minutes and got deleted). Do NOT
            # create a new Pod just because there isn't one anymore --
            # that would resurrect something that's supposed to be over.
            return ReconcileResult(phase="Deleted")

        # This is a genuinely new CjPod. Build the "this Pod belongs to
        # that CjPod" pointer, then create it.
        owner = OwnerInfo(
            api_version="interview.cj.dev/v1",
            kind="CjPod",
            name=cjpod_name,
            uid=cjpod_uid,
        )
        pods.create(cjpod_name, cjpod_namespace, pod_template, owner)

        # Ask to be checked again in exactly POD_LIFETIME -- the Pod is
        # brand new, so its full 3-minute window is still ahead of it.
        return ReconcileResult(
            phase="Running",
            requeue_after_seconds=POD_LIFETIME.total_seconds(),
        )

    # Step 2: a Pod with this name DOES exist. Before doing anything
    # else to it, make sure it's actually ours.
    if not is_owned_by(existing, cjpod_uid):
        return ReconcileResult(
            phase="Error",
            message=f"existing Pod {existing.namespace}/{existing.name} is not owned by this CjPod",
        )

    # Step 3: it's our Pod. Work out the deadline from ITS OWN
    # creation time -- not from any counter or timer this process has
    # been tracking. This one line is the entire restart-recovery
    # story: no matter how long the operator was down, this
    # calculation always produces the correct deadline, because
    # existing.creation_timestamp came straight from Kubernetes, which
    # never forgets it.
    delete_at = existing.creation_timestamp + POD_LIFETIME

    if now < delete_at:
        # Not due yet -- report how many seconds remain, so whatever
        # scheduled this reconcile can wait exactly that long before
        # checking again instead of polling blindly.
        remaining = (delete_at - now).total_seconds()
        return ReconcileResult(phase="Running", requeue_after_seconds=remaining)

    # Step 4: the deadline has passed. Possibly it passed WHILE the
    # operator was down entirely -- that's fine. "At least 3 minutes"
    # is satisfied either way, since the Pod has been running for at
    # least 3 minutes regardless of exactly when we got around to
    # noticing. pods.delete() takes the Pod's UID as a precondition
    # (see KubernetesPodsAPI.delete in k8s_pods.py) so that if this
    # exact Pod had somehow already been replaced by a different one
    # with the same name, the delete call fails safely instead of
    # deleting the wrong object.
    pods.delete(existing.name, existing.namespace, existing.uid)
    return ReconcileResult(phase="Deleted")
