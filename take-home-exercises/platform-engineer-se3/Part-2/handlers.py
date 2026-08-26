"""
kopf wiring -- the thin layer that connects kopf's event system to the
pure logic in reconciler.py. Nothing in this file makes any real
decisions; it just gathers the right arguments from kopf and hands
them to reconcile().

Run this operator with:
    kopf run -m cjpod_operator.handlers --all-namespaces
"""
from datetime import datetime, timezone

import kopf
from kubernetes import client

from .k8s_pods import KubernetesPodsAPI
from .reconciler import reconcile

# These three identify our CustomResourceDefinition to kopf: group is
# the API "namespace" (interview.cj.dev), version is v1, plural is
# what shows up in `kubectl get cjpods`. They match config/crd/cjpod-crd.yaml
# exactly -- if you rename the CRD there, update these too.
GROUP = "interview.cj.dev"
VERSION = "v1"
PLURAL = "cjpods"

# How often @kopf.timer below re-checks each CjPod. Deletion happens
# within this many seconds of the exact 3-minute mark, not to the
# millisecond -- see design-notes.md for why that tradeoff was chosen
# deliberately over a more precise (but less provably restart-safe)
# alternative.
POLL_INTERVAL_SECONDS = 10


def _pods_api() -> KubernetesPodsAPI:
    """Builds a real, cluster-talking PodsAPI. client.CoreV1Api() picks
    up cluster credentials automatically -- from the in-cluster service
    account when running as a Pod, or from your local kubeconfig when
    running on your laptop against a cluster like kind."""
    return KubernetesPodsAPI(client.CoreV1Api())


@kopf.on.create(GROUP, VERSION, PLURAL)
def on_create(spec, name, namespace, uid, patch, logger, **_):
    """
    Fires exactly once, the moment a new CjPod is created -- kopf
    inspects this function's parameter names and automatically passes
    in the matching pieces of the CjPod object (its spec, its name and
    namespace, its uid, and so on). `**_` soaks up any extra arguments
    kopf offers that we don't need by name, so this function doesn't
    break if a future kopf version adds more.

    This handler exists purely for fast feedback -- it creates the Pod
    immediately instead of making you wait for the first @kopf.timer
    tick below (up to POLL_INTERVAL_SECONDS later) to see anything happen.
    """
    result = reconcile(
        cjpod_name=name,
        cjpod_namespace=namespace,
        cjpod_uid=uid,
        pod_template=spec["template"],  # the .spec.template block from the CjPod YAML
        already_deleted=False,           # it was JUST created, so this is always false here
        pods=_pods_api(),
        now=datetime.now(timezone.utc),  # always use UTC for anything compared against Kubernetes timestamps, which are UTC
    )
    _apply_status(patch, result)
    logger.info("cjpod %s/%s created -> phase=%s", namespace, name, result.phase)


@kopf.timer(GROUP, VERSION, PLURAL, interval=POLL_INTERVAL_SECONDS)
def on_timer(spec, name, namespace, uid, status, patch, logger, **_):
    """
    Fires every POLL_INTERVAL_SECONDS, for as long as a CjPod exists --
    including CjPods this particular operator PROCESS didn't create.
    That last point is what makes restart-recovery work with no special
    code: kopf automatically re-attaches this timer to every
    already-existing CjPod when the operator (re)starts, so a freshly
    started process just resumes ticking for everything it finds,
    exactly as if nothing had ever stopped.

    `status` here is the CjPod's current .status block (whatever the
    previous reconcile wrote via patch.status) -- we read
    status["phase"] out of it to know whether this CjPod's lifecycle
    already finished, so we don't accidentally recreate a Pod that was
    deleted on schedule.
    """
    already_deleted = (status or {}).get("phase") == "Deleted"
    result = reconcile(
        cjpod_name=name,
        cjpod_namespace=namespace,
        cjpod_uid=uid,
        pod_template=spec["template"],
        already_deleted=already_deleted,
        pods=_pods_api(),
        now=datetime.now(timezone.utc),
    )
    _apply_status(patch, result)
    if result.phase == "Deleted" and not already_deleted:
        # Only log the "just happened" transition once, not on every
        # future tick for a CjPod that's already finished.
        logger.info("cjpod %s/%s: 3 minute window elapsed, pod deleted", namespace, name)


@kopf.on.resume(GROUP, VERSION, PLURAL)
def on_resume(name, namespace, logger, **_):
    """
    kopf calls this once per CjPod when the operator starts up and
    discovers resources that already existed before it began watching
    -- i.e. exactly the restart scenario. Not required for correctness
    (the @kopf.timer above already re-establishes the deletion check
    on its own), but genuinely useful for visibility: this log line is
    how you'd prove, while demoing this to yourself or an interviewer,
    that the operator actually noticed it was picking up pre-existing
    work rather than starting fresh and ignoring it.
    """
    logger.info("cjpod %s/%s rediscovered after operator (re)start", namespace, name)


def _apply_status(patch, result):
    """
    Writes a ReconcileResult onto kopf's `patch` object. kopf collects
    everything written to `patch` during a handler call and applies it
    as a single Kubernetes API patch request once the handler returns
    -- we don't call the Kubernetes API directly here at all, we just
    stage the change and let kopf handle actually sending it.
    """
    patch.status["phase"] = result.phase
    if result.message:
        patch.status["message"] = result.message
