# Design notes: CjPod operator

## How do you handle restarts?

The 3-minute deadline is never stored in the operator's own memory —
it's derived fresh, every check, from the Pod's own
`metadata.creationTimestamp`, which Kubernetes sets once and stores in etcd:

```python
delete_at = existing.creation_timestamp + POD_LIFETIME
```

Because of this, recovery isn't special code. kopf's `@kopf.timer`
automatically re-attaches to every CjPod that already exists when the
operator restarts, and that first tick runs through the exact same
reconcile() logic as any other. If the deadline already passed while
the operator was down, it just deletes the Pod immediately. It can delete late, but never earlier than 3 min.

## Why did you choose your timing approach?

Anchoring the deadline to creationTimestamp instead of
tracking elapsed time myself was because any in-memory timer dies with the
process in this case the operator. Kubernetes' own metadata doesn't die.
Using a recurring `@kopf.timer` helps pick back up from where we left by finding the cjpod resource and calculating the delete_at time again and again without the need for storing the time itself.

## What tradeoffs did you make?

Precision vs. certainty. Deletion happens within 10 seconds of the deadline, not to the millisecond. Keeping the reconciler logic seperate makes it more detached and resuable, but requires more code. This way we were able to run tests in milliseconds, without actually waiting for the actual scenario.
