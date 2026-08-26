"""
The real, cluster-talking implementation of the PodsAPI protocol
defined in reconciler.py. This is the ONLY module in the whole project
that imports the `kubernetes` client library -- everything else works
with the plain-data PodInfo/OwnerInfo objects instead, which is what
lets reconciler.py be tested without a cluster at all.

If you're comparing this to the Go version: this file plays the same
role client-go's typed client plays there -- the thin layer that turns
"create a Pod" into an actual HTTP call to the Kubernetes API server.
"""
from __future__ import annotations

from typing import Optional

from kubernetes import client
from kubernetes.client.rest import ApiException

from .reconciler import OwnerInfo, PodInfo


class KubernetesPodsAPI:
    """
    Wraps a kubernetes.client.CoreV1Api (the "core" Kubernetes API --
    Pods, Services, Namespaces, etc. all live here) and exposes exactly
    the three methods reconciler.py's PodsAPI Protocol expects. Note
    this class doesn't explicitly inherit from PodsAPI -- with a
    Protocol, it doesn't need to. Python checks the *shape* (does it
    have get/create/delete with the right signatures?), not the family
    tree.
    """

    def __init__(self, core_v1: client.CoreV1Api):
        self._core_v1 = core_v1

    def get(self, name: str, namespace: str) -> Optional[PodInfo]:
        """Fetch one Pod by name, or return None if it doesn't exist.
        Kubernetes signals "not found" as an HTTP 404, which the
        client library surfaces as an ApiException -- we catch that
        specific case and turn it into a clean None, exactly the way
        a real Python API is expected to behave (vs. forcing every
        caller to catch a low-level HTTP exception)."""
        try:
            pod = self._core_v1.read_namespaced_pod(name, namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise  # anything else (permissions, network, etc.) is a real problem -- let it propagate
        return _to_pod_info(pod)

    def create(self, name: str, namespace: str, pod_spec: dict, owner: OwnerInfo) -> PodInfo:
        """
        Builds a Pod manifest (as a plain Python dict -- the
        kubernetes client library accepts raw dicts shaped like YAML,
        which is often simpler than building its typed model classes
        by hand) and creates it. The ownerReferences entry here is
        what Kubernetes itself uses for automatic garbage collection:
        delete the CjPod, and Kubernetes deletes this Pod too, with no
        code of ours involved.
        """
        body = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": (pod_spec.get("metadata") or {}).get("labels", {}),
                "ownerReferences": [{
                    "apiVersion": owner.api_version,
                    "kind": owner.kind,
                    "name": owner.name,
                    "uid": owner.uid,
                    "controller": True,          # marks this as THE controlling owner -- what is_owned_by() checks for
                    "blockOwnerDeletion": True,  # prevents the CjPod from being force-deleted while this Pod still exists
                }],
            },
            "spec": pod_spec["spec"],
        }
        pod = self._core_v1.create_namespaced_pod(namespace, body)
        return _to_pod_info(pod)

    def delete(self, name: str, namespace: str, uid: str) -> None:
        """
        Deletes a Pod, but only if its current UID still matches `uid`
        -- this is the "precondition" mentioned in reconciler.py's
        comments. Without it, a delete-by-name call would happily
        delete WHATEVER Pod currently has this name, even if it's not
        the exact one we looked at a moment ago. With it, the API
        server rejects the request (HTTP 409 Conflict) if the object
        changed underneath us, and we treat that -- along with an
        already-gone 404 -- as "fine, nothing more to do here" rather
        than an error worth crashing over.
        """
        try:
            self._core_v1.delete_namespaced_pod(
                name,
                namespace,
                body=client.V1DeleteOptions(
                    preconditions=client.V1Preconditions(uid=uid)
                ),
            )
        except ApiException as e:
            if e.status in (404, 409):
                return
            raise


def _to_pod_info(pod) -> PodInfo:
    """
    Translates a real Pod object (as returned by the kubernetes client
    library) into our own plain PodInfo. The leading underscore is a
    Python convention meaning "internal to this module, not part of
    its public API" -- nothing outside k8s_pods.py should call this
    directly.
    """
    owner_refs = pod.metadata.owner_references or []
    # Find the ONE owner reference (if any) marked as the controller --
    # a Pod could theoretically have multiple non-controller owner
    # references, but only one controller=true reference is allowed.
    controller_ref = next((r for r in owner_refs if r.controller), None)
    return PodInfo(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        uid=pod.metadata.uid,
        creation_timestamp=pod.metadata.creation_timestamp,  # the client library already parses this into a real datetime for us
        owner_uid=controller_ref.uid if controller_ref else None,
        owner_controller=controller_ref is not None,
    )
