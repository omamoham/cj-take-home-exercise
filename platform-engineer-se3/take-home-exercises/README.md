# Platform Engineer (SE3) — Take-Home Assignment

## Overview

This assignment has three parts:

1. **Cluster networking design** — design the VPC/CNI/routing for two EKS clusters under real constraints.
2. **Operator design** — design a Kubernetes operator for a custom resource. Write the reconciler logic and explain your approach.
3. **Alerting review** — read a live Alertmanager configuration and explain why a critical alert paged nobody.

These are the hardest, most job-specific parts of the role. All three are things AI can draft but only an engineer who understands the mechanics can defend — expect to walk through your reasoning live in a later stage.

Part 3 is short. Answer it in prose; a paragraph per question is plenty.

---

## AI Policy

AI tools are allowed and expected. If you use AI, include `ai-usage.md` with:

1. Which tool you used
2. Prompt summaries or prompts
3. Suggestions you accepted
4. Suggestions you rejected
5. What you independently verified

We are not evaluating whether you used AI. We are evaluating whether you used it responsibly — and you will be asked to defend your design without it in a later stage.

---

## Part 1: Cluster Networking Design

CJ runs production workloads on EKS. You need to configure networking for two EKS clusters in two VPCs — `10.0.1.0/24` (Cluster 1) and `10.0.2.0/24` (Cluster 2) — both connected to the corporate network via VPN (IP range `10.0.0.0/8`).

Requirements:

- **Separate VPCs:** Each cluster is in a different VPC.
- **VPN connectivity:** Both clusters are connected to the corporate network via VPN.
- **IP addressing:** No additional IPs can be used in `10.0.0.0/8`.
- **Large number of pods:** Both clusters run thousands of pods.
- **Exposing endpoints:** Cluster 2 exposes multiple endpoints that must be reachable from within the corporate network.
- **Traffic routing:** Pods in Cluster 1 need to reach endpoints in Cluster 2 *without* using the VPN.

### Deliverable — `networking-design.md`

Describe how you would configure networking to meet every requirement above. Be specific about:

- How you avoid IP exhaustion given thousands of pods on a `/24`, without consuming more of `10.0.0.0/8`.
- The exact Kubernetes/CNI mechanism that makes your IP plan work — not just "add a CIDR," but what the VPC CNI needs in order to actually assign pod IPs from it.
- How Cluster 1 pods reach Cluster 2 endpoints without traversing the VPN, including what has to change beyond "connect the two VPCs."
- How Cluster 2's endpoints are exposed to the corporate network.
- Any assumptions, tradeoffs, or failure modes in your design.

---

## Part 2: Kubernetes Operator Design

Write a Kubernetes operator that manages a custom resource, `CjPod`, which defines a Pod spec under `spec.template`. The operator should:

- Create a Pod based on the `spec.template` of the CjPod resource, with the same name and namespace.
- Delete the Pod exactly 3 minutes after creation, but only the Pod it created.
- Ensure graceful recovery: if the operator is terminated before deletion, it should clean up after restarting.
- Ensure the Pod runs for at least 3 minutes regardless of operator restarts.
- Implement unit tests to validate the operator's logic.

Example CjPod resource:

```yaml
apiVersion: interview.cj.dev/v1
kind: CjPod
metadata:
  name: cjpod-123
spec:
  template:
    metadata: {}
    spec:
      containers:
        - name: abc
          image: nginx
          ports:
            - containerPort: 80
```

### Deliverables

- `operator.go` (or equivalent) — reconciler implementation
- `operator_test.go` (or equivalent) — unit tests
- `design-notes.md` — explain your key design decisions: how you handle restarts, why you chose your timing approach, any tradeoffs

---

## Part 3: Alerting Review

A persistent volume filled up in production and took a service down. The alert that should have
caught it was firing in Prometheus for 40 minutes beforehand. Nobody was paged.

The alert was confirmed in the `firing` state, with these labels:

```
alertname = KubePersistentVolumeFillingUp
namespace = platform
severity  = critical
cluster   = prod-us-east-1
```

Prometheus was healthy and Alertmanager was up, reachable, and had delivered other
notifications that hour. This is the Alertmanager configuration that was live at the time:

```yaml
route:
  receiver: default-slack
  group_by: [alertname, cluster]
  routes:
    - matchers:
        - namespace =~ "platform|monitoring"
      receiver: platform-slack
      group_interval: 5m

    - matchers:
        - severity = "critical"
      receiver: pagerduty-critical
      group_wait: 30s

inhibit_rules:
  - source_matchers:
      - alertname = "ClusterUpgradeInProgress"
    target_matchers:
      - severity = "critical"
    equal: [cluster]

receivers:
  - name: default-slack
    slack_configs:
      - channel: "#alerts-firehose"
  - name: platform-slack
    slack_configs:
      - channel: "#platform-alerts"
  - name: pagerduty-critical
    pagerduty_configs:
      - routing_key: <redacted>
```

### Deliverable — `alerting-review.md`

Answer:

- **Trace this alert through the configuration.** Which receiver did it reach, and why that one?
  Be specific about how Alertmanager walks the route tree.
- There is a second, independent reason a `severity: critical` alert in this cluster can go
  unpaged even if the routing were fixed. What is it, and under what circumstances does it bite?
- What would you change? Say what each change fixes, and call out anything you would want to
  verify before shipping it rather than assuming.
- This configuration was reviewed and approved. What about it made the problem easy to miss on
  review?

---

## `ai-usage.md` (required if you used AI)

See AI policy above.
