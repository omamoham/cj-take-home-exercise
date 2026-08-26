# Alerting Review

1. Which receiver did it reach, and why that one? Be specific about how
Alertmanager walks the route tree.

It reached the platform-slack reciever. The alerts get routed sequentially. Because the namespace =~ "platform|monitoring" matcher got evaulated first and matched. It got routed to that receiver and stopped, without checking the other route. To fix this we can add continue: true to the first route in the sequence to ensure the sibling routes are evaluated as well.

2. There is a second, independent reason a `severity: critical` alert in
this cluster can go unpaged even if the routing were fixed. What is
it, and under what circumstances does it bite?

inhibit_rules:
  - source_matchers:
      - alertname = "ClusterUpgradeInProgress"
    target_matchers:
      - severity = "critical"
    equal: [cluster]

The inhibit_rules target_matchers are very broad. Any important alert unreleated to the clusterupgrade can also go unpaged. To fix this we should have more specific matchers to ensure the rest of the unrelated alerts still get routed to the right receivers. These could be good target matchers, "KubeNodeNotReady|KubeletDown|KubeNodeUnreachable|KubeAPIDown|KubePodNotReady". Much narrower in scope and accurate.

3. Say what each change fixes, and call out anything you would want to
verify before shipping it rather than assuming.

By adding the continue: true to the namespace route, we could ensure both route conditions are evaluated. We could also reorder the routes, to solve this particular issue, but that would give the same issue if the severity=critical but the namespace was monitoring too. WOuld only route to the pagerduty-critical in that scenario as well. So adding continue=true is the best approach.

For the inhibit rule, I would narrow down the target_matchers to be more source alert specific. Right now it is very broad and will result in unneccesary suppression of actual critical unrelated alerts. Also making sure along with the cluster we ensure the namespace is same as the source_matcher would help narrow it down even further.

4. This configuration was reviewed and approved. What about it made the
problem easy to miss on review?

It's easy to miss a line of continue: true when the rest of the logic is coherant. TO a human brain that can reason better, this seems totally correct, but computer are mechanical in nature. They interpret instructions literally. So having and additional reviewer of codce can help in such circumstances.